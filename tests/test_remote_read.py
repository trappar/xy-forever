"""Test027 remote read: B's READ_FILE path format, result layout, spans; no device execution.
(A's decisions: the file engine, tests/test_file_engine.py.)"""
from pathlib import Path
import re
import struct
import unittest

import xyf_hosttest
from xy_forever.xyf_build import A_FIND_CALLS, A_MARKER_CALLS, A_RENAME_CALLS, SERVER_CALLS, WRITE_ENTRIES, peer_spans, server_spans
from xy_forever.census import (REMOTE_READ_ACTIONS, REMOTE_READ_BYTES, REMOTE_READ_FIELDS, REMOTE_READ_MAGIC,
                                   STORAGE_DIAGNOSTIC_WORDS, remote_read)
from xy_forever.paths import STOCK_121

ROOT = Path(__file__).resolve().parents[1]

A = {name: i for i, name in enumerate(REMOTE_READ_ACTIONS)}


class RemoteReadTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lib = xyf_hosttest.module('storage_server')

    def test_read_file_path_format(self):
        good = (b'/yaffs2/firmware.version', b'/yaffs2/xy-forever/state.txt', b'/yaffs2/a')
        bad = (b'', b'/yaffs2/', b'/yaffs2//a', b'/yaffs2/./a', b'/yaffs2/../a', b'/yaffs2/a/..',
               b'/yaffs2/a/', b'yaffs2/a', b'/yaffs20/a', b'/yaffs2/' + b'a' * 240)
        for path in good:
            self.assertTrue(self.lib.valid_path(path), path)
        for path in bad:
            self.assertFalse(self.lib.valid_path(path), path)

    def test_result_layout_matches_header_and_decoder(self):
        text = (ROOT / 'target/storage_server.hpp').read_text()
        body = text[text.index('struct Result {'):text.index('};', text.index('struct Result {'))]
        names = []
        for decl in re.findall(r'uint32_t ([^;]+);', body):
            names += [n.strip() for n in decl.split(',') if '[' not in n]
        self.assertEqual(names, REMOTE_READ_FIELDS)
        self.assertEqual(self.lib.result_bytes(), REMOTE_READ_BYTES)
        words = [0] * (REMOTE_READ_BYTES // 4)
        values = dict(magic=REMOTE_READ_MAGIC, version=1, role=1, enabled=1, b_fd=3, b_bytes=10, b_close=0,
                      log_count=4, b_catalogs=5)
        for k, v in values.items():
            words[REMOTE_READ_FIELDS.index(k)] = v
        at = len(REMOTE_READ_FIELDS)
        words[at:at + 8] = struct.unpack('<8I', b'1.2.1+0@abc'.ljust(32, b'\0'))
        words[at + 8] = 0x08 | A['answer_mount'] << 8
        words[at + 9] = 0x29 | A['run_open'] << 8 | 3 << 16
        words[at + 10] = 0x25 | 0x66 << 8 | 0xffff << 16        # the file engine's actions (0x60..)
        words[at + 11] = 0x18 | 0x41 << 8                        # install (0x40..)
        block = struct.pack('<%dI' % len(words), *words)
        out = remote_read([dict(address=0x8ab05000, bytes=len(block), offset=0)], block)
        self.assertEqual(out['text'], '1.2.1+0@abc')
        self.assertEqual([e['action'] for e in out['log']],
                         ['answer_mount', 'run_open', 'refuse_protected_path', 'install_select'])
        self.assertEqual((out['b_fd'], out['b_bytes']), (3, 10))
        self.assertEqual(REMOTE_READ_BYTES, 4 * (24 + 8 + 16 + STORAGE_DIAGNOSTIC_WORDS))

    def test_spans_cover_calls_and_avoid_l1_instruction(self):
        spans = server_spans()
        self.assertEqual(len(spans), 7)
        for address, length, _ in spans:
            self.assertFalse(0x11a00000 <= address < 0x11a0c000)
        covered = [(a, n) for a, n, _ in spans + peer_spans()]
        for call in SERVER_CALLS | A_MARKER_CALLS | A_RENAME_CALLS | A_FIND_CALLS:
            self.assertTrue(any(a <= int(call, 16) < a + n for a, n in covered), call)
        # The stock read-write mount path and write entries are never called directly.
        self.assertFalse({'%08x' % a for a in WRITE_ENTRIES} &
                         (SERVER_CALLS | A_MARKER_CALLS | A_RENAME_CALLS | A_FIND_CALLS))
        source = ''.join((ROOT / 'target' / n).read_text()
                          for n in ('storage_server.cpp', 'storage_server_engine.ipp'))
        for address in WRITE_ENTRIES:
            self.assertNotIn('0x%08x' % address, source.lower())

    def test_spans_match_bootloader(self):
        boot = STOCK_121 / 'bootloader.ldr'
        if not boot.exists():
            self.skipTest('private bootloader absent')
        import zlib
        from xy_forever.build_formats import Firmware
        firmware = Firmware(boot.read_bytes())
        for address, length, crc in server_spans():
            self.assertEqual(zlib.crc32(firmware.read(address, length)), crc, hex(address))


if __name__ == '__main__':
    unittest.main()
