"""The save-time log: xyf_host.py save-times' table over target/xyf_save_log.hpp's lines (the laps, line and merge:
tests/native/save_log_test.cpp)."""

import re
import unittest
from pathlib import Path

from xy_forever import xyf_host

ROOT = Path(__file__).resolve().parents[1]


class SaveLogTest(unittest.TestCase):
    def test_line_merge_and_table(self):
        # The line the C++ test checks byte for byte (tests/native/save_log_test.cpp).
        cpp = (ROOT / 'tests/native/save_log_test.cpp').read_text()
        want = cpp[cpp.index('const char want[] =') : cpp.index(';', cpp.index('const char want[] ='))]
        first = ''.join(re.findall(r'"((?:[^"\\]|\\.)*)"', want)).replace('\\n', '')
        self.assertTrue(
            first.startswith('kind=save result=ok mods=30 memory=expanded added=1 removed=0 total=2205 '), first
        )
        fields = dict(f.split('=') for f in first.split())
        self.assertEqual(
            (fields['list'], fields['check'], fields['link1'], fields['link2'], fields['zero']),
            ('200', '5', '1000', '1000', '0'),
        )
        self.assertEqual((fields['written'], fields['bytes']), ('7', '4096000000'))
        table = xyf_host.save_times_table(first + '\n' + first.replace('mods=30', 'mods=29') + '\n')
        self.assertIn('link1', table)
        self.assertNotIn('zero', table)  # 0 everywhere: left out
        self.assertRegex(table, r'mods\s+30\s+29')


if __name__ == '__main__':
    unittest.main()
