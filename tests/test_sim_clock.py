"""#88: the simulator's virtual device clock (sim/clock.cpp) in test runs. Device time moves only under the
simulator's control: a processor stopped on the host (as a loaded machine deschedules it) stops it, so no device
time-out runs out meanwhile; idle, it passes on its own; advance fast-forwards it with the link kept alive; and
an interactive run keeps real time. Needs clang++ and the private stock 1.2.1 images (skipped without them). No
device access."""
import os
from pathlib import Path
import signal
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from xy_forever import sim                                                 # noqa: E402
import xyf_host as host                                                    # noqa: E402

UNAVAILABLE = sim.available() or (None if sim.STOCK_OS.is_file() else 'private stock 1.2.1 os.ldr absent')
PATH = '/yaffs2/xy-forever/state-a.txt'


@unittest.skipIf(UNAVAILABLE, UNAVAILABLE)
class VirtualClock(unittest.TestCase):
    virtual = True

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='xyf-sim-')
        self.dir = Path(self.tmp.name)
        sim.seed(self.dir / 'yaffs2')
        self.sim = sim.Simulator(self.dir / 'yaffs2', self.dir / 'bootpart.bin', build_dir=sim.build(),
                                 log=self.dir / 'log.txt', virtual=self.virtual).start()
        self.dev = host.Device(host.TcpPort(self.sim.address), on_log=None)
        self.assertEqual(self.dev.hello()[0], 0)
        self.assertEqual(self.dev.read_file(PATH)[0], 0)                  # storage up: reads are link requests

    def tearDown(self):
        self.dev.t.close()
        self.sim.stop()
        self.tmp.cleanup()

    def clock(self):
        return self.sim.status()['clock_ms']

    def test_a_stopped_processor_stops_the_clock(self):
        """A stopped for 2 s of host time with B's read waiting on it: the device clock stays put (B's request
        bound never runs out), and the read completes once A runs again."""
        a = self.sim.status()['pids'][0]
        before = self.clock()
        os.kill(a, signal.SIGSTOP)
        reply = {}
        try:
            thread = threading.Thread(target=lambda: reply.update(r=self.dev.read_file(PATH)), daemon=True)
            thread.start()
            time.sleep(2.0)
            self.assertLess(self.clock() - before, 100)
        finally:
            os.kill(a, signal.SIGCONT)
        thread.join(30.0)
        self.assertEqual(reply['r'][0], 0)
        self.assertEqual(self.sim.status()['a_boots'], 1)

    def test_idle_time_passes_and_advance_fast_forwards(self):
        """Idle, the clock moves on its own (at most at host pace); advance moves it 60 s in far less host time,
        with B's keepalives and A's answers on the way: A still serves B (no idle restart), and B reads."""
        before = self.clock()
        deadline = time.monotonic() + 10.0
        while self.clock() - before < 200:
            self.assertLess(time.monotonic(), deadline, 'the idle clock never moved')
            time.sleep(0.01)
        before, started = self.clock(), time.monotonic()
        self.sim.advance(60000)
        self.assertGreaterEqual(self.clock() - before, 60000)
        self.assertLess(time.monotonic() - started, 30.0)
        status = self.sim.status()
        self.assertEqual((status['a'], status['b'], status['a_boots'], status['b_boots']), ('running', 'running', 1, 1))
        self.assertEqual(self.dev.read_file(PATH)[0], 0)

    def test_jump_moves_the_clock_at_once(self):
        before = self.clock()
        self.sim.jump(5000)
        self.assertGreaterEqual(self.clock() - before, 5000)


@unittest.skipIf(UNAVAILABLE, UNAVAILABLE)
class RealTimeClock(VirtualClock):
    """python3 -m xy_forever.sim (and Simulator(virtual=False)): the host's clock."""
    virtual = False

    def test_a_stopped_processor_stops_the_clock(self):
        self.assertEqual(self.sim.status()['virtual'], 0)
        before, started = self.clock(), time.monotonic()
        time.sleep(0.3)
        self.assertGreaterEqual(self.clock() - before, int((time.monotonic() - started) * 1000) - 50)

    def test_idle_time_passes_and_advance_fast_forwards(self):
        before = self.clock()
        self.sim.advance(5000)                                             # one jump
        self.assertGreaterEqual(self.clock() - before, 5000)


if __name__ == '__main__':
    unittest.main()
