"""Sound-pack layout and manifest admission through the stock filesystem limits."""

import unittest

import xyf_hosttest
from test_native_package import package

PREFIX = 'firmware/blackfin/content/dlc/'
MARKER = 'firmware/blackfin/content/partial_upgrade'
MANIFEST = PREFIX + 'pack/manifest.json'
ASSET = PREFIX + 'pack/samples/test.wav'


class SoundPackArchiveTest(unittest.TestCase):
    def inspect(self, rows):
        status, folder, files, size, limit = xyf_hosttest.module('sound_pack_archive').admit(package(rows))
        return (0 if status == 'ok' else 1), status, folder, (files, size, limit)

    def test_accepts_one_pack_and_excludes_the_marker(self):
        manifest = b'{"folder":"pack"}'
        code, status, folder, sizes = self.inspect(
            [('firmware/', b''), (PREFIX + 'pack/', b''), (MARKER, b''), (MANIFEST, manifest), (ASSET, b'sample')]
        )
        self.assertEqual((code, status, folder), (0, 'ok', 'pack'))
        self.assertEqual(sizes, (2, len(manifest) + 6, 320 * 1024 * 1024))

    def test_checks_the_marker_folder_and_exact_archive_scope(self):
        for marker, manifest, other in (
            (None, b'{"folder":"pack"}', ASSET),
            (b'payload', b'{"folder":"pack"}', ASSET),
            (b'', b'{"folder":"other"}', ASSET),
            (b'', b'{}', ASSET),
            (b'', b'{"folder":"pack"}', PREFIX + 'another/sample.wav'),
            (b'', b'{"folder":"pack"}', 'firmware/blackfin/bin/os.ldr'),
            (b'', b'{"folder":"pack"}', PREFIX + 'pack/bootloader.ldr'),
        ):
            rows = [(MANIFEST, manifest), (other, b'data')]
            if marker is not None:
                rows.append((MARKER, marker))
            with self.subTest(marker=marker, manifest=manifest, member=other):
                self.assertEqual(self.inspect(rows)[0], 1)

    def test_checks_directory_depth_for_both_destinations(self):
        valid = [(MARKER, b''), (MANIFEST, b'{"folder":"pack"}')]
        limit = PREFIX + 'pack/samples/set/type/one.wav'
        too_deep = PREFIX + 'pack/samples/set/type/extra/one.wav'
        self.assertEqual(self.inspect(valid + [(limit, b'sample')])[0], 0)
        self.assertEqual(self.inspect(valid + [(too_deep, b'sample')])[:2], (1, 'bounds'))
