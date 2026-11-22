"""The suite's budgets (tests/conftest.py): a fast test is held to its CPU time, so one that waits passes and
one that computes past the limit fails the run, named. The planted run fixes the scale at 1
(XYF_BUDGETS_SCALE): a busy machine's probe would raise the 0.3 s limit past the planted 0.35 s."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLANTED = """import time, unittest
class Planted(unittest.TestCase):
    def test_waits(self):
        time.sleep(0.35)

    def test_computes(self):
        end = time.process_time() + 0.35
        while time.process_time() < end:
            pass
"""


@pytest.mark.gate
class BudgetsTest(unittest.TestCase):
    @pytest.mark.uses('tests/conftest.py')
    def test_cpu_time_fails_waiting_does_not(self):
        with tempfile.TemporaryDirectory(prefix='xyf-budget-') as tmp:
            (Path(tmp) / 'test_planted.py').write_text(PLANTED)
            (Path(tmp) / 'pytest.ini').write_text('[pytest]\npython_classes = Planted\n')
            run = subprocess.run(
                [
                    sys.executable,
                    '-m',
                    'pytest',
                    '-p',
                    'conftest',
                    '-p',
                    'no:xdist',
                    '-m',
                    'fast',
                    str(Path(tmp) / 'test_planted.py'),
                ],
                cwd=tmp,
                env=dict(os.environ, PYTHONPATH=str(ROOT / 'tests'), XYF_BUDGETS='on', XYF_BUDGETS_SCALE='1'),
                capture_output=True,
                text=True,
                timeout=60,
            )
        output = run.stdout + run.stderr
        self.assertEqual(run.returncode, 1, output)
        self.assertIn('2 passed', output)
        self.assertRegex(
            output,
            r'BUDGET: \S*test_planted.py::Planted::test_computes took 0\.\d+ s of CPU: over the fast '
            r"tier's [\d.]+ s a test",
        )
        self.assertNotIn('test_waits took', output)
