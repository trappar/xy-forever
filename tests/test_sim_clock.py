"""#88, #240: the simulator's device clock (sim/clock.cpp). Device time moves only under the simulator's control: at
rest it stands still (blocking, no CPU) until something is due; advance moves it on with the link kept alive; jump
moves it at once. --idle-wall (interactive runs only) lets idle device time follow the host's clock. Needs clang++
and the private stock 1.2.1 images (skipped without them). No device access."""
from pathlib import Path
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from xy_forever import sim                                                 # noqa: E402
import xyf_host as host                                                    # noqa: E402

UNAVAILABLE = sim.available() or (None if sim.STOCK_OS.is_file() else 'private stock 1.2.1 os.ldr absent')
PATH = '/yaffs2/xy-forever/state-a.txt'


@unittest.skipIf(UNAVAILABLE, UNAVAILABLE)
class EventClock(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='xyf-sim-')
        self.dir = Path(self.tmp.name)
        sim.seed(self.dir / 'yaffs2')
        self.sim = sim.Simulator(self.dir / 'yaffs2', self.dir / 'bootpart.bin', build_dir=sim.build(),
                                 log=self.dir / 'log.txt').start()
        self.dev = host.Device(host.TcpPort(self.sim.address), on_log=None)
        self.assertEqual(self.dev.hello()[0], 0)
        self.assertEqual(self.dev.read_file(PATH)[0], 0)                  # storage up: reads are link requests

    def tearDown(self):
        self.dev.t.close()
        self.sim.stop()
        self.tmp.cleanup()

    def clock(self):
        return self.sim.status()['clock_ms']

    def test_rest_stands_still_and_advance_fast_forwards(self):
        """At rest the clock stands still however many status calls come; advance moves it 60 s with B's keepalives
        and A's answers on the way: A still serves B (no idle restart), and B reads."""
        self.sim.settle()
        before, steps = self.clock(), self.sim.status()["steps"]
        for _ in range(20):
            self.assertEqual((self.clock(), self.sim.status()['steps']), (before, steps))
        self.sim.advance(60000)
        self.assertGreaterEqual(self.clock() - before, 60000)
        status = self.sim.status()
        self.assertEqual((status['a'], status['b'], status['a_boots'], status['b_boots']), ('running', 'running', 1, 1))
        self.assertEqual(self.dev.read_file(PATH)[0], 0)

    def test_jump_moves_the_clock_at_once(self):
        before = self.clock()
        self.sim.jump(5000)
        self.assertGreaterEqual(self.clock() - before, 5000)


@unittest.skipIf(UNAVAILABLE, UNAVAILABLE)
class IdleWall(unittest.TestCase):
    """python -m xy_forever.sim --idle-wall (mise run sim): idle device time follows the host's clock, so a person
    at the web app sees the device's own timers fire. The only wall-time wait in the simulator tests: every
    other test runs without the flag, on device time that moves only by advance, until and settle."""

    def test_idle_device_time_follows_the_wall(self):
        with tempfile.TemporaryDirectory(prefix='xyf-sim-') as tmp:
            root = sim.seed(Path(tmp) / 'yaffs2')
            running = sim.Simulator(root, Path(tmp) / 'bootpart.bin', build_dir=sim.build(), log=Path(tmp) / 'log.txt')
            running.argv = lambda *a, argv=running.argv: argv(*a) + ['--idle-wall']
            with running.start():
                before = running.status()['clock_ms']
                time.sleep(0.5)
                self.assertGreater(running.status()['clock_ms'], before)


if __name__ == '__main__':
    unittest.main()
