"""Power-safe A/B state slots: host build of the device C++."""
from pathlib import Path
import subprocess
import tempfile
import unittest

from xy_forever import hostbuild

ROOT = Path(__file__).resolve().parents[1]


class StateTest(unittest.TestCase):
    def test_slots(self):
        with tempfile.TemporaryDirectory(prefix='xyf-state-') as tmp:
            binary = Path(tmp) / 'state'
            hostbuild.run(['/usr/bin/clang++', '-std=c++11', '-Wall', '-Wextra', '-Werror',
                           '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                           str(ROOT / 'target/xyf_store.cpp'), str(ROOT / 'target/xyf_crc32.cpp'),
                           str(ROOT / 'target/xyf_state.cpp'), str(ROOT / 'target/xyf_state_test.cpp'),
                           '-o', str(binary)], check=True, capture_output=True, text=True)
            result = subprocess.run([str(binary)], check=True, capture_output=True, text=True)
            self.assertRegex(result.stdout, r'[1-9][0-9]* checks passed')


if __name__ == '__main__':
    unittest.main()
