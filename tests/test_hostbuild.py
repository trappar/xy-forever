"""xy_forever/hostbuild.py, the tests' compile cache: an output is reused only for the same command
line, compiler and file contents (sources and every header the compile read), never for a failure."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from xy_forever import hostbuild  # noqa: E402

CXX = '/usr/bin/clang++'


@unittest.skipUnless(Path(CXX).exists(), 'no clang++')
class HostBuildTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='xyf-hostbuild-')
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        patch = mock.patch.dict(os.environ, {'XYF_HOST_BUILDS': str(self.dir / 'cache')})
        patch.start()
        self.addCleanup(patch.stop)
        self.runs = 0

    def source(self, folder, value, body='int main() { printf("%d", VALUE); return 0; }'):
        folder = self.dir / folder
        folder.mkdir(exist_ok=True)
        (folder / 'value.hpp').write_text('#define VALUE %s\n' % value)
        (folder / 'main.cpp').write_text('#include <stdio.h>\n#include "value.hpp"\n' + body + '\n')
        return folder

    def build(self, folder, out=None, extra=()):
        """(the program's output, compiles run) for one hostbuild.run."""
        self.runs += 1
        out = out or self.dir / ('program-%d' % self.runs)
        compiles, real = [], subprocess.run

        def counting(argv, *args, **kwargs):
            if '-o' in argv:
                compiles.append(argv)
            return real(argv, *args, **kwargs)
        with mock.patch.object(hostbuild.subprocess, 'run', counting):
            hostbuild.run([CXX, '-std=c++11', '-Wall', '-Werror', *extra, '-I', str(folder),
                           str(folder / 'main.cpp'), '-o', str(out)], check=True, capture_output=True)
        return real([str(out)], capture_output=True, text=True, check=True).stdout, len(compiles)

    def test_reuse_only_for_the_same_inputs(self):
        src = self.source('src', 1)
        self.assertEqual(self.build(src), ('1', 1))
        self.assertEqual(self.build(src), ('1', 0))                    # reused
        (src / 'value.hpp').write_text('#define VALUE 2\n')           # a header the compile read
        self.assertEqual(self.build(src), ('2', 1))
        self.assertEqual(self.build(src, extra=['-DUNUSED=1']), ('2', 1))   # another command line
        moved = self.source('elsewhere', 2)                           # the same files in a new folder
        self.assertEqual(self.build(moved), ('2', 0))

    def test_a_failure_is_not_kept_and_a_hit_writes_a_new_file(self):
        src = self.source('src', 3, body='int main() { return VALUE }')
        with self.assertRaises(subprocess.CalledProcessError):
            self.build(src)
        self.assertEqual(list((self.dir / 'cache').glob('*')), [])
        src = self.source('src', 3)
        out = self.dir / 'program'
        self.assertEqual(self.build(src, out), ('3', 1))
        inode = out.stat().st_ino
        self.assertEqual(self.build(src, out), ('3', 0))               # ran again: not rewritten in place
        self.assertNotEqual(out.stat().st_ino, inode)

    def test_no_cache_without_one_output(self):
        src = self.source('src', 4)
        with mock.patch.object(hostbuild.subprocess, 'run', wraps=subprocess.run) as run:
            for _ in range(2):
                hostbuild.run([CXX, '-fsyntax-only', str(src / 'main.cpp'), '-I', str(src)], check=True)
        self.assertEqual(run.call_count, 2)
        self.assertFalse((self.dir / 'cache').exists())


if __name__ == '__main__':
    unittest.main()
