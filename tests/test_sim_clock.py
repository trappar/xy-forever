"""#88, #240: the simulator's device clock (sim/clock.cpp). Device time moves only under the simulator's control: at
rest it stands still (blocking, no CPU) until something is due; advance moves it on with the link kept alive; jump
moves it at once. --idle-wall (interactive runs only) lets idle device time follow the host's clock. Needs clang++
and the private stock 1.2.1 images (skipped without them). No device access."""

import struct
import tempfile
import time
import unittest
from pathlib import Path

import pytest

from xy_forever import bounds, sim
from xy_forever import usb_proto as P
from xy_forever import xyf_host as host

UNAVAILABLE = sim.available() or (None if sim.STOCK_OS.is_file() else 'private stock 1.2.1 os.ldr absent')
PATH = '/yaffs2/xy-forever/state-a.txt'


@unittest.skipIf(UNAVAILABLE, UNAVAILABLE)
class EventClock(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='xyf-sim-')
        self.dir = Path(self.tmp.name)
        sim.seed(self.dir / 'yaffs2')
        self.sim = sim.Simulator(
            self.dir / 'yaffs2', self.dir / 'bootpart.bin', binary=sim.build(), log=self.dir / 'log.txt'
        ).start()
        self.dev = host.Device(host.TcpPort(self.sim.address), on_log=None)
        self.assertEqual(self.dev.hello()[0], 0)
        self.assertEqual(self.dev.read_file(PATH)[0], 0)  # storage up: reads are link requests

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
        before, steps = self.clock(), self.sim.status()['steps']
        for _ in range(20):
            self.assertEqual((self.clock(), self.sim.status()['steps']), (before, steps))
        self.sim.advance(60000)
        self.assertGreaterEqual(self.clock() - before, 60000)
        status = self.sim.status()
        self.assertEqual((status['a'], status['b'], status['a_boots'], status['b_boots']), ('running', 'running', 1, 1))
        self.assertEqual(self.dev.read_file(PATH)[0], 0)

    @pytest.mark.sim
    def test_a_request_sent_while_b_waits_on_a_is_answered_when_the_wait_ends(self):
        """#229: a host request that comes while B waits on A inside a menu pass (a busy host's next request during
        the last one's close) is answered once that wait ends, with no more device time: B never rests on unread
        bytes. hold_clock keeps B's wait open however slowly the host sends."""
        self.sim.settle()
        self.sim.set('hold_clock', 1)
        self.sim.set('a_silent', 1)
        self.dev.t.write(P.encode(P.ids.STORAGE_SPACE, 1, struct.pack('<I', 1)))  # B asks A: no answer comes
        self.sim.until(lambda: self.sim.status()['a_silenced'], "B's request to A")
        asked = self.clock()
        self.dev.t.write(P.encode(P.HELLO, 2))  # read by B only after the pass
        self.sim.set('a_silent', 0)
        replies = {}

        def read(seconds):
            for kind, seq, body in self.dev.parser.feed(self.dev.t.read(seconds)):
                replies[seq] = (kind, struct.unpack_from('<I', body)[0])

        # B's 5 s bound passes in 1 ms steps, and no step after it: B's next menu pass needs no more device time.
        self.sim.advance(asked + 4990 - self.clock())
        for _ in range(50):
            read(0.02)  # B wrote its answer before it went idle (advance returned)
            if replies:
                break
            self.sim.advance(1)
        deadline = time.monotonic() + bounds.SIM_CALL_S  # a hang bound only
        while len(replies) < 2 and time.monotonic() < deadline:
            read(0.2)
        self.assertEqual(replies, {1: (P.ids.STORAGE_SPACE | P.REPLY, P.ids.S_LINK), 2: (P.HELLO | P.REPLY, 0)})

    @pytest.mark.sim
    def test_a_host_pause_reaches_its_own_simulator_only(self):
        """Kept: #229 round 7, every host pause (xyf_host, conformance, device_pass) goes this way. Two simulators at
        once, each with a client: a pause through one's transport moves that one's clock by the pause and leaves
        the other's clock and client alone (a control port inferred from the endpoint's reached no simulator, a
        wall-clock sleep, or another's endpoint, which dropped that one's client)."""
        sim.seed(self.dir / 'other')
        other = sim.Simulator(self.dir / 'other', self.dir / 'other.bin', binary=self.sim.binary).start()
        try:
            them = host.Device(host.TcpPort(other.address), on_log=None)
            self.sim.settle()
            mine, theirs = self.clock(), other.status()['clock_ms']
            self.dev.t.pause(5.0)
            self.assertGreaterEqual(self.clock() - mine, 5000)
            self.assertEqual(other.status()['clock_ms'], theirs)
            self.assertEqual(them.hello()[0], 0)
            them.t.close()
        finally:
            other.stop()


if __name__ == '__main__':
    unittest.main()
