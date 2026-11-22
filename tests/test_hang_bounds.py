"""A planted hang is killed and named within its bound (#230, xy_forever/bounds.py).

scripts/run_tests.py: a test that sleeps past its bound (the watchdog) and a process that runs past its
bound (SIGTERM, then SIGKILL) are each reported as HUNG: <test id> with a traceback, and the child the
test started (in the worker's process group) is gone. scripts/check.py: a step past its bound fails.
The simulator: a stalled one (SIGSTOP) fails a host call within SIM_CALL_S and stop() ends its whole
group; an idle one exits on its own at its deadline (xyf_sim --deadline-s) and says so.
"""
import importlib.util
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import xyf_host as host  # noqa: E402
from xy_forever import bounds, sim  # noqa: E402

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


def group_gone(pgid):
    try:
        os.killpg(pgid, 0)
    except OSError:
        return True
    return False


class RunnerBoundsTest(unittest.TestCase):
    def planted(self, *bound):
        """Runs the planted hang under run_tests.py with bound: (output, seconds, the child's pid)."""
        with tempfile.TemporaryDirectory(prefix='xyf-hang-') as tmp:
            (Path(tmp) / 'planted_hang.py').write_text(PLANTED)
            pid_file = Path(tmp) / 'pid'
            env = dict(os.environ, PYTHONPATH=tmp, XYF_PLANTED_PID=str(pid_file))
            start = time.monotonic()
            run = subprocess.run([sys.executable, str(ROOT / 'scripts/run_tests.py'), '--jobs', '2', *bound,
                                  'planted_hang'], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
            seconds = time.monotonic() - start
            self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
            return run.stdout + run.stderr, seconds, int(pid_file.read_text())

    def check(self, output, seconds, child, bound):
        self.assertIn('HUNG: planted_hang.Hang.test_sleeps', output)
        self.assertIn('in test_sleeps', output)                     # the hung test's traceback
        self.assertLess(seconds, bound + SLACK, output)
        deadline = time.monotonic() + 3
        while not gone(child) and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertTrue(gone(child), 'the hung test\'s child survived')

    def test_a_test_past_its_bound(self):
        self.check(*self.planted('--test-timeout', '2'), 2)

    def test_a_process_past_its_bound(self):
        self.check(*self.planted('--process-timeout', '2'), 2)


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


@unittest.skipIf(sim.available(), sim.available())
class SimulatorBoundsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='xyf-hang-sim-')
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.root = sim.seed(self.dir / 'yaffs2')

    def start(self, **options):
        s = sim.Simulator(self.root, self.dir / 'bootpart.bin', log=self.dir / 'log.txt', **options).start()
        self.addCleanup(s.stop)
        return s

    def test_a_stalled_simulator_fails_the_call_in_time_and_its_group_ends(self):
        s = self.start()
        dev = host.Device(host.TcpPort(s.address), on_log=None)
        self.addCleanup(dev.t.close)
        self.assertEqual(dev.hello()[0], 0)
        pgid = s.process.pid
        os.killpg(pgid, signal.SIGSTOP)                            # stalled: nothing answers
        start = time.monotonic()
        with self.assertRaisesRegex(TimeoutError, 'no reply to 0x[0-9a-f]+ within 2.0 s'):
            dev.call(host.P.HELLO, timeout=600)
        self.assertLess(time.monotonic() - start, bounds.SIM_CALL_S + 1)
        start = time.monotonic()
        s.stop()
        self.assertLess(time.monotonic() - start, SLACK)
        self.assertTrue(group_gone(pgid), 'a stalled simulator\'s group survived stop()')

    def test_a_simulator_exits_at_its_deadline(self):
        s = self.start(deadline_s=2)
        process, pgid = s.process, s.process.pid
        self.assertEqual(process.wait(timeout=2 + SLACK), 124)
        deadline = time.monotonic() + 3
        while not group_gone(pgid) and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertTrue(group_gone(pgid), 'the processors outlived the board')
        with self.assertRaises(sim.DeadlinePassed):
            s.status()
        self.assertIn('deadline: 2.0 s passed', (self.dir / 'log.txt').read_text())


if __name__ == '__main__':
    unittest.main()
