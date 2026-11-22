"""The converge plan (target/xyf_converge.hpp, #197): its table, its rules (a build is switched to; a switch brings the
record and the default), and for every combination of inputs and actual state, running the plan then planning
again gives nothing. Host-built with clang++ (tests/converge_test.cpp). No device access."""
from pathlib import Path
import subprocess
import tempfile
import unittest

import pytest
from xy_forever import hostbuild
ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.gate
class Converge(unittest.TestCase):
    def test_plan_table_and_planning_twice_gives_nothing(self):
        with tempfile.TemporaryDirectory(prefix='xyf-converge-') as tmp:
            binary = Path(tmp) / 'converge'
            hostbuild.run(['/usr/bin/clang++', '-std=c++14', '-Wall', '-Wextra', '-Werror',
                           '-fsanitize=address,undefined', '-fno-omit-frame-pointer', '-I', str(ROOT / 'target'),
                           str(ROOT / 'tests/converge_test.cpp'), '-o', str(binary)],
                          check=True, capture_output=True, text=True)
            out = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(out.returncode, 0, out.stderr[-4000:])
            self.assertIn('converge: ok', out.stdout)


if __name__ == '__main__':
    unittest.main()
