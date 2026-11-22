"""A planted hang is killed and named within its bound (#230, xy_forever/bounds.py).

pytest (tests/xyf_bounds.py): a test that sleeps past its bound is reported as HUNG: <test id> with every
thread's traceback, its xdist worker's whole process group ends (the child the test started with it), and
the run fails naming the test. scripts/check.py: a step past its bound fails. The simulator: a stalled one
(SIGSTOP) fails a host call within SIM_CALL_S and stop() ends its whole group; an idle one exits on its own
at its deadline (xyf_sim --deadline-s) and says so.
"""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

import pytest

ROOT = Path(__file__).resolve().parents[1]

PLANTED = '''import os, subprocess, sys, time, unittest
class Hang(unittest.TestCase):
    def test_sleeps(self):
        child = subprocess.Popen(['sleep', '300'])
        with open(os.environ['XYF_PLANTED_PID'], 'w') as f:
            f.write(str(child.pid))
        time.sleep(300)
'''
SLACK = 8.0             # s: interpreter starts, SIGTERM's grace (procgroup.GRACE), a loaded host


def gone(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False



@pytest.mark.gate
class RunnerBoundsTest(unittest.TestCase):
    @pytest.mark.uses('tests/xyf_bounds.py')
    def test_a_test_past_its_bound(self):
        bound = 1.0
        with tempfile.TemporaryDirectory(prefix='xyf-hang-') as tmp:
            (Path(tmp) / 'test_planted_hang.py').write_text(PLANTED)
            (Path(tmp) / 'pytest.ini').write_text('[pytest]\n')
            pid_file = Path(tmp) / 'pid'
            env = dict(os.environ, PYTHONPATH=str(ROOT / 'tests'),
                       XYF_PLANTED_PID=str(pid_file))
            start = time.monotonic()
            run = subprocess.run([sys.executable, '-m', 'pytest', '-p', 'xyf_bounds', '-p', 'no:cacheprovider', '-n', '1',
                                  '-o', 'timeout=%g' % bound, 'test_planted_hang.py'], cwd=tmp, env=env,
                                 capture_output=True, text=True, timeout=60)
            seconds, output = time.monotonic() - start, run.stdout + run.stderr
            self.assertEqual(run.returncode, 1, output)
            child = int(pid_file.read_text())
        self.assertIn('HUNG: test_planted_hang.py::Hang::test_sleeps', output)
        self.assertIn('in test_sleeps', output)                     # the hung test's traceback
        self.assertIn("crashed while running 'test_planted_hang.py::Hang::test_sleeps'", output)
        self.assertLess(seconds, bound + SLACK, output)
        deadline = time.monotonic() + 3
        while not gone(child) and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertTrue(gone(child), 'the hung test\'s child survived')


@pytest.mark.gate
class CheckStepBoundTest(unittest.TestCase):
    def test_a_step_past_its_bound_fails_named(self):
        spec = importlib.util.spec_from_file_location('check_script', ROOT / 'scripts/check.py')
        check = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(check)
        with tempfile.TemporaryDirectory(prefix='xyf-hang-') as tmp:
            log = Path(tmp) / 'log.txt'
            step = check.step('stalls', [sys.executable, '-c', 'import time; time.sleep(300)'], tmp, timeout=1)
            start = time.monotonic()
            results, _ = check.run_steps([step], log)
            self.assertLess(time.monotonic() - start, 1 + SLACK)
            self.assertEqual((results[0]['result'], results[0]['exit']), ('fail', 124))
            self.assertIn('HUNG: stalls ran past its 1 s bound', log.read_text())


if __name__ == '__main__':
    unittest.main()
