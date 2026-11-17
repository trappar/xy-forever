"""Host preparation of XY Forever library entries (no device access)."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from xy_forever.xyf_entry import check_layout, info_text, make_entry
from xy_forever.modpatch import entry_layout_text
from xy_forever.paths import STOCK_121


class EntryTest(unittest.TestCase):
    def test_info_text_matches_device_format(self):
        data = b'abc'
        text = info_text('OP-XY 1.2.1', '1.2.1+0', 'official', data)
        self.assertEqual(text, 'name=OP-XY 1.2.1\nversion=1.2.1+0\nkind=official\nbytes=3\ncrc32=%08x\n'
                         % (zlib.crc32(data) & 0xffffffff))
        with self.assertRaises(ValueError):
            info_text('a\nb', '', 'custom', data)

    def test_layout_limits(self):
        good = {'format': 'opxy-layout/1', 'profile': 'opxy-1.2.1', 'machines': {'karplus': {'engine': 36, 'page': 84}}}
        self.assertIsNone(check_layout(json.dumps(good)))
        self.assertEqual(check_layout(json.dumps(dict(good, selected=['karplus']))), 1)
        for bad in (dict(good, format='x'), dict(good, machines={'Bad': {'engine': 1, 'page': 1}}),
                    dict(good, machines={'x': {'engine': 1, 'effect': 2, 'page': 1}})):
            with self.assertRaises(ValueError):
                check_layout(json.dumps(bad))

    def test_make_entry_with_real_ldr(self):
        payload = STOCK_121 / 'os.ldr'                             # the stock 1.2.1 OS (setup.py import-stock)
        if not payload.exists():
            self.skipTest('stock 1.2.1 os.ldr absent (run scripts/setup.py import-stock)')
        with tempfile.TemporaryDirectory() as tmp:
            layout = Path(tmp) / 'layout.json'                     # as open-xy's website writes it
            layout.write_text(entry_layout_text('opxy-1.2.1', ['karplus'], [], {'karplus': {'engine': 36, 'page': 84}}))
            result = make_entry(payload, tmp, 'opxy-121', 'OP-XY 1.2.1', '1.2.1+0', 'official', layout)
            self.assertEqual(result['machines'], 1)
            folder = Path(result['folder'])
            self.assertEqual((folder / 'os.ldr').read_bytes(), payload.read_bytes())
            self.assertIn('kind=official\n', (folder / 'info.txt').read_text())
            with self.assertRaises(ValueError):
                make_entry(payload, tmp, 'opxy-121', 'again')      # existing folder
            with self.assertRaises(ValueError):
                make_entry(payload, tmp, 'Bad', 'x')


if __name__ == '__main__':
    unittest.main()
