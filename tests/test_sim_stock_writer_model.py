"""The simulator's real stock writer shim: both role routes, ABI, retries and commit/cut ordering.

Synthetic media only. The XOR representation tests buffer ownership and ciphertext readback;
it supplies no cryptographic evidence about the real bootloader.
"""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from xy_forever import hostbuild


class StockWriterModel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='xyf-stock-writer-')
        cls.exe = Path(cls.tmp.name) / 'writer'
        hostbuild.run(['/usr/bin/clang++', '-std=c++17', '-O1', '-Wall', '-Wextra', '-Werror',
                       '-I', str(ROOT / 'sim'), str(ROOT / 'tests/sim_stock_writer_test.cpp'),
                       '-o', str(cls.exe)], check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @pytest.mark.gate
    def test_both_roles_match_stock_failure_and_commit_paths(self):
        for role in (0, 1):
            for case in ('success', 'multichunk', 'invalid', 'initial', 'retry', 'body', 'header', 'early', 'aligned',
                         'stuck', 'cutbody', 'cutcommit', 'cutold'):
                with self.subTest(role=role, case=case):
                    result = subprocess.run([str(self.exe), str(role), case], capture_output=True, text=True,
                                            timeout=10)  # test harness bound, never the stock routine's
                    self.assertEqual(result.returncode, 0, result.stderr)
