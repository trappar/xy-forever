"""XY Forever menu state machine: host build of the device C++."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from xy_forever import hostbuild  # noqa: E402


class MenuTest(unittest.TestCase):
    def test_state_machine(self):
        with tempfile.TemporaryDirectory(prefix='xyf-menu-') as tmp:
            binary = Path(tmp) / 'menu'
            hostbuild.run(['/usr/bin/clang++', '-std=c++11', '-Wall', '-Wextra', '-Werror',
                           '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                           str(ROOT / 'target/xyf_store.cpp'), str(ROOT / 'target/xyf_layout.cpp'), str(ROOT / 'target/xyf_info.cpp'), str(ROOT / 'target/xyf_library.cpp'), str(ROOT / 'target/xyf_state.cpp'), str(ROOT / 'target/xyf_crc32.cpp'),
                           str(ROOT / 'target/xyf_catalog.cpp'), str(ROOT / 'target/xyf_menu.cpp'),
                           str(ROOT / 'target/xyf_menu_test.cpp'), '-o', str(binary)],
                          check=True, capture_output=True, text=True)
            result = subprocess.run([str(binary)], check=True, capture_output=True, text=True)
            self.assertRegex(result.stdout, r'[1-9][0-9]* checks passed')


if __name__ == '__main__':
    unittest.main()
