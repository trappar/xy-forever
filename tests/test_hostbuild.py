"""xy_forever/hostbuild.py, the tests' compile cache: an output is reused only for the same command
line, compiler and file contents (sources and every header the compile read), never for a failure."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import pytest

from xy_forever import bounds, hostbuild

CXX = '/usr/bin/clang++'


@pytest.mark.gate
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
        compiles, real, compile = [], subprocess.run, hostbuild.procgroup.run

        def counting(argv, *args, **kwargs):
            if '-o' in argv:
                compiles.append(argv)
            return compile(argv, *args, **kwargs)
        with mock.patch.object(hostbuild.procgroup, 'run', counting):
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
        with mock.patch.object(hostbuild.procgroup, 'run', wraps=hostbuild.procgroup.run) as run:
            for _ in range(2):
                hostbuild.run([CXX, '-fsyntax-only', str(src / 'main.cpp'), '-I', str(src)], check=True)
        self.assertEqual(run.call_count, 2)
        self.assertFalse((self.dir / 'cache').exists())


@pytest.mark.gate
class CacheRaceTest(unittest.TestCase):
    """Entries publish whole (a stale one is replaced, a concurrent one kept), and pruning skips an entry
    another process removed meanwhile (#230)."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='xyf-hostbuild-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def entry(self, name, text):
        folder = self.root / name
        folder.mkdir()
        (folder / 'output').write_text(text)
        return folder

    def test_publish_replaces_a_stale_entry_in_one_rename(self):
        stale, work = self.entry('key', 'stale'), self.entry('new-1', 'fresh')
        hostbuild._publish(self.root, work, stale)
        self.assertEqual((self.root / 'key' / 'output').read_text(), 'fresh')
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ['key'])

    def test_publish_into_an_empty_slot(self):
        work = self.entry('new-1', 'fresh')
        hostbuild._publish(self.root, work, self.root / 'key')
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ['key'])

    def test_prune_skips_an_entry_removed_meanwhile(self):
        for n in range(4):
            self.entry('e%d' % n, str(n))
        gone, real = self.root / 'e0', Path.stat

        def racing(path, *args, **kwargs):
            if path == gone and os.path.isdir(gone):
                shutil.rmtree(gone)                    # another process pruned it between listing and stat
            return real(path, *args, **kwargs)
        with mock.patch.object(Path, 'stat', racing):
            hostbuild.prune(self.root, keep=2)
        self.assertEqual(len(list(self.root.iterdir())), 2)


@pytest.mark.gate
@unittest.skipUnless(Path(CXX).exists(), 'no clang++')
class CompileBoundTest(unittest.TestCase):
    def test_a_compile_past_its_bound_ends(self):
        with mock.patch.object(bounds, 'COMPILE_S', 0.5), mock.patch.dict(os.environ, {'XYF_HOST_BUILDS': 'off'}):
            with self.assertRaises(subprocess.TimeoutExpired):
                hostbuild.run([sys.executable, '-c', 'import time; time.sleep(30)', '-o', 'x'])


if __name__ == '__main__':
    unittest.main()
