"""Stock count/queue/body fields stay distinct at the production A quarantine seam."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class Framing(unittest.TestCase):
    def test_stock_request_layout_and_malformed_quarantine(self):
        compiler = shutil.which('clang++') or shutil.which('g++')
        if not compiler:
            self.skipTest('C++ compiler unavailable')
        with tempfile.TemporaryDirectory(prefix='xyf-stage-framing-') as tmp:
            binary = Path(tmp) / 'framing'
            built = subprocess.run([compiler, '-std=c++11', str(ROOT / 'tests/a_stage_framing_test.cpp'),
                                    '-o', str(binary)], capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stderr)
            ran = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(ran.returncode, 0, ran.stderr)
