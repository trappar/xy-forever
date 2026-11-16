"""The save-time log (plan 8.8 step 0): target/xyf_save_log.hpp's laps, line and merge (last 20 records),
and xyf_host.py save-times' table over its lines."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from xy_forever import hostbuild  # noqa: E402
import xyf_host  # noqa: E402

PROGRAM = r'''
#include "xyf_save_log.hpp"
#include <stdio.h>
#include <string.h>
using namespace xyf::save_log;
int main() {
    Times t;
    begin(t, k_save, 1000, p_list);
    lap(t, 1200, p_check);                 // list 200
    lap(t, 1205, p_job_base + 5);          // check 5, then link1
    lap(t, 2205, p_job_base + 7);          // link1 1000, then link2
    t.mods = 30; t.expanded = 1; t.added = 1; t.ok = 1; t.written = 7; t.bytes = 4096000000u;
    static char text[max_text + 64];
    static char line[max_line];
    const unsigned n = format(line, sizeof(line), t, 3205);   // link2 1000 (running)
    fwrite(line, 1, n, stdout);
    unsigned length = 0;
    for (unsigned i = 0; i != 25; ++i) {
        char one[16];
        const int k = snprintf(one, sizeof(one), "kind=save n=%u\n", i);
        length = merge(text, length, one, unsigned(k));
    }
    fwrite(text, 1, length, stdout);
    if (format(line, 40, t, 3205)) return 1;       // too small: 0
    return 0;
}
'''


class SaveLogTest(unittest.TestCase):
    def test_line_merge_and_table(self):
        with tempfile.TemporaryDirectory(prefix='xyf-save-log-') as tmp:
            source, binary = Path(tmp) / 'log.cpp', Path(tmp) / 'log'
            source.write_text(PROGRAM)
            hostbuild.run(['/usr/bin/clang++', '-std=c++14', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
                           '-I', str(ROOT / 'target'), str(source), '-o', str(binary)], check=True, capture_output=True, text=True)
            out = subprocess.run([str(binary)], check=True, capture_output=True, text=True).stdout.splitlines()
        first = out[0]
        self.assertTrue(first.startswith('kind=save result=ok mods=30 memory=expanded added=1 removed=0 total=2205 '), first)
        fields = dict(f.split('=') for f in first.split())
        self.assertEqual((fields['list'], fields['check'], fields['link1'], fields['link2'], fields['zero']),
                         ('200', '5', '1000', '1000', '0'))
        self.assertEqual((fields['written'], fields['bytes']), ('7', '4096000000'))
        self.assertEqual(out[1:], ['kind=save n=%d' % i for i in range(5, 25)])   # the last 20
        table = xyf_host.save_times_table(first + '\n' + first.replace('mods=30', 'mods=29') + '\n')
        self.assertIn('link1', table)
        self.assertNotIn('zero', table)                                          # 0 everywhere: left out
        self.assertRegex(table, r'mods\s+30\s+29')


if __name__ == '__main__':
    unittest.main()
