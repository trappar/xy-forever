from pathlib import Path
import subprocess
import tempfile
import unittest

from xy_forever import hostbuild

ROOT = Path(__file__).resolve().parents[1]

class RomModelTests(unittest.TestCase):
    def test_context_layout_and_bounded_source_with_sanitizers(self):
        with tempfile.TemporaryDirectory(prefix='xyf-rom-test-') as directory:
            binary = Path(directory) / 'rom-model'
            hostbuild.run(['/usr/bin/clang++', '-std=c++11', '-Wall', '-Wextra', '-Werror',
                           '-fsanitize=address,undefined', str(ROOT / 'target/rom_model_test.cpp'),
                           '-o', str(binary)], check=True)
            subprocess.run([str(binary)], check=True, timeout=20)
