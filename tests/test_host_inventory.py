"""The host compares live file bytes and free space using only read requests."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

import xyf_host
from xy_forever import host_inventory, usb_proto as P


class ReadOnlyDevice:
    contents = {'op1_factory.json': b'catalog', 'presets/a': b'a' * 1024, 'empty': b''}

    def call(self, command, payload, timeout):
        assert command == P.ids.STORAGE_SPACE and payload == struct.pack('<I', 1)
        return 0, struct.pack('<IQ', 1, 2**33)

    def list_dir(self, remote):
        assert remote.startswith('/yaffs2/content')
        folder = remote[len('/yaffs2/content'):].strip('/')
        prefix = folder + '/' if folder else ''
        entries = set()
        for path, data in self.contents.items():
            if path.startswith(prefix):
                name, slash, _ = path[len(prefix):].partition('/')
                entries.add((1 if slash else 0, 0 if slash else len(data), name.encode()))
        return 0, sorted(entries)

    def read_file(self, remote, offset):
        data = self.contents[remote[len('/yaffs2/content/'):]]
        return 0, data[offset:offset + P.ids.MAX_READ]


class HostInventoryTest(unittest.TestCase):
    def test_cli_exact_matches_and_changed_file_across_releases(self):
        dev = ReadOnlyDevice()
        with tempfile.TemporaryDirectory() as folder, patch.object(host_inventory.paths, 'XYF', Path(folder)):
            for ver, files in [('v1', dev.contents), ('v2', dev.contents), ('v3', {'presets/a': b'b', 'b': b'x'})]:
                out = Path(folder, 'content-manifests', ver)
                out.mkdir(parents=True)
                (out / 'content.sha256').write_text(''.join(
                    '%s  %s\n' % (hashlib.sha256(data).hexdigest(), rel) for rel, data in sorted(files.items())))
            with patch.object(xyf_host, 'connect', return_value=(dev, 'fake', 'XYF1;proto=1.21')), \
                    contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(xyf_host.main(['content', 'status']), 0)
            report = json.loads(out.getvalue().split('\n', 1)[1])
        self.assertEqual(report['exact_matches'], ['v1', 'v2'])
        self.assertEqual(report['catalog_sha256'], hashlib.sha256(b'catalog').hexdigest())
        self.assertEqual(report['catalog_matches'], ['v1', 'v2'])
        self.assertEqual(report['free_bytes'], 2**33)
        self.assertEqual(report['differences']['v3'], dict(changed=['presets/a'], extra_files=['empty', 'op1_factory.json'],
                                                           missing=['b']))
        self.assertEqual(report['matches_no_official_file'], [])

    def test_unknown_catalog_and_leftovers_include_special_entries(self):
        dev = ReadOnlyDevice()
        listing = dev.list_dir
        with patch.object(dev, 'list_dir', side_effect=lambda p: (0, listing(p)[1] + [(2, 0, b'link')])
                          if p == '/yaffs2/content' else listing(p)):
            files, special = host_inventory.read_inventory(dev)
        report = host_inventory.compare_sets(files, special, {'v1': {'op1_factory.json': ('other hash', 7)}})
        self.assertEqual(report['matches_no_official_file'], ['empty', 'op1_factory.json', 'presets/a'])
        self.assertEqual(report['special_entries'], ['link'])
        self.assertEqual(report['exact_matches'], [])
        self.assertFalse(host_inventory.compare_sets(files, special, {'v1': files})['exact_matches'])

    def test_incomplete_and_failed_reads_cannot_return_a_match(self):
        dev = ReadOnlyDevice()
        with patch.object(dev, 'read_file', return_value=(0, b'')), self.assertRaisesRegex(ValueError, 'listed .*read'):
            host_inventory.read_inventory(dev)
        with patch.object(dev, 'list_dir', return_value=(P.ids.S_STORAGE, [])), self.assertRaisesRegex(OSError, 'list'):
            host_inventory.read_inventory(dev)
        with patch.object(dev, 'read_file', return_value=(P.ids.S_LINK, b'')), self.assertRaisesRegex(OSError, 'read'):
            host_inventory.read_inventory(dev)

    def test_truncated_directory_reply_cannot_masquerade_as_an_empty_folder(self):
        dev = xyf_host.Device(None)
        with patch.object(dev, 'call', return_value=(0, b'')), self.assertRaisesRegex(OSError, 'file.list: short'):
            host_inventory.read_inventory(dev)

    def test_newer_space_reply_is_refused(self):
        with patch.object(ReadOnlyDevice, 'call', return_value=(0, struct.pack('<IQ', 2, 10))), \
                self.assertRaisesRegex(ValueError, 'unsupported storage.space reply version 2'):
            host_inventory.free_bytes(ReadOnlyDevice())
