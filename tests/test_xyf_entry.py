"""Host preparation of XY Forever library entries (no device access)."""

import json
import tempfile
import unittest
import zlib
from pathlib import Path

from xy_forever.paths import STOCK_121
from xy_forever.xyf_entry import info_text, make_entry


class EntryTest(unittest.TestCase):
    def test_info_text_matches_device_format(self):
        data = b'abc'
        text = info_text('OP-XY 1.2.1', '1.2.1+0', 'official', data)
        self.assertEqual(
            text,
            'type=firmware\nname=OP-XY 1.2.1\nversion=1.2.1+0\nkind=official\nbytes=3\ncrc32=%08x\n'
            'sha256=ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad\n'
            % (zlib.crc32(data) & 0xFFFFFFFF),
        )
        with self.assertRaises(ValueError):
            info_text('a\nb', '', 'custom', data)

    def test_make_entry_with_real_ldr(self):
        payload = STOCK_121 / 'os.ldr'  # the stock 1.2.1 OS (setup.py import-stock)
        if not payload.exists():
            self.skipTest('stock 1.2.1 os.ldr absent (run uv run scripts/setup.py import-stock)')
        with tempfile.TemporaryDirectory() as tmp:
            result = make_entry(payload, tmp, 'opxy-121', 'OP-XY 1.2.1', '1.2.1+0', 'official')
            folder = Path(result['folder'])
            self.assertEqual((folder / 'os.ldr').read_bytes(), payload.read_bytes())
            self.assertEqual(json.loads((folder / 'manifest.json').read_bytes())['kind'], 'official')
            self.assertFalse((folder / 'info.txt').exists())
            self.assertFalse((folder / 'layout.json').exists())
            with self.assertRaises(ValueError):
                make_entry(payload, tmp, 'opxy-121', 'again')  # existing folder
            with self.assertRaises(ValueError):
                make_entry(payload, tmp, 'Bad', 'x')


if __name__ == '__main__':
    unittest.main()
