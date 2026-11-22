"""#88, #240: the simulator's device clock (sim/clock.cpp). Device time moves only under the simulator's control: at
rest it stands still (blocking, no CPU) until something is due; advance moves it on with the link kept alive; jump
moves it at once. --idle-wall (interactive runs only) lets idle device time follow the host's clock. Needs clang++
and the private stock 1.2.1 images (skipped without them). No device access."""
from pathlib import Path
import tempfile
import unittest

import pytest

from xy_forever import sim
from xy_forever import xyf_host as host

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

    @pytest.mark.sim
    def test_rest_stands_still_and_advance_fast_forwards(self):
        """Kept: The frozen clock is what keeps this tier compute-bound. At rest the clock stands still however many status calls come; advance moves it 60 s with B's keepalives
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

if __name__ == '__main__':
    unittest.main()
