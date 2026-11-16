"""Readonly allocator guard, with adversarial metadata and bounded reads."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from xy_forever import hostbuild  # noqa: E402


class ColdHeapTests(unittest.TestCase):
    def test_allocator_state_below_initial_outer_staging(self):
        with tempfile.TemporaryDirectory(prefix='cold-heap-') as temporary:
            binary=Path(temporary)/'heap'
            hostbuild.run(['/usr/bin/clang++','-std=c++11','-Wall','-Wextra','-Werror',
                           '-fsanitize=address,undefined','-fno-omit-frame-pointer',
                           str(ROOT/'target/cold_heap.cpp'),str(ROOT/'target/cold_heap_test.cpp'),
                           '-o',str(binary)],check=True,capture_output=True,text=True)
            result=subprocess.run([str(binary)],check=True,capture_output=True,text=True)
            self.assertIn('14 cold heap guard checks passed',result.stdout)


if __name__=='__main__':unittest.main()
