"""The C++ unit tests (tests/native/*_test.cpp, doctest), in the doctest binary `uv sync` built (hosttest/): one
run of every case, in the gate tier (one sanitized process start per file cost too much CPU on CI). A failure
names its file and case."""

import subprocess
import unittest
from pathlib import Path

import pytest
import xyf_hosttest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.gate
class TestDoctest(unittest.TestCase):
    def test_every_case(self):
        files = sorted(p.relative_to(ROOT).as_posix() for p in ROOT.glob('tests/native/*_test.cpp'))
        xyf_hosttest.used(*files)
        run = subprocess.run(
            [str(xyf_hosttest.DOCTEST), '--no-intro', '--no-version'], capture_output=True, text=True, timeout=60
        )
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
