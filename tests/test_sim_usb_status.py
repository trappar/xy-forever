"""8.7: the menu's USB mark and activity line in the simulator (usb_service host_seen, host_activity).

The simulator does not draw the screen: sim/devices.cpp logs each change of what key_ui would draw ("menu: usb
mark on|off", "menu: usb line: TEXT|none") where key_ui redraws it (every menu pass, and key_ui::host_view
when a frame arrives). The mark comes with the first frame, stays while the host talks and goes out
host_seen_ms after the last one; the line names a staged write while it runs (receiving, then writing) and a
mods.apply job started over USB while it runs, and is gone afterwards. Needs clang++ and the private stock
1.2.1 images (skipped without them). No device access."""
import hashlib
from pathlib import Path
import re
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
from xy_forever import usb_proto as P                                      # noqa: E402
import test_sim_mod_job as J                                               # noqa: E402

SEEN_MS, ACTIVITY_MS = 5000, 2000                                          # usb_service.hpp host_seen_ms, host_activity_ms
WRITE_PATH = '/yaffs2/xy-forever/log/sim/usb-status.bin'


@unittest.skipIf(J.UNAVAILABLE, J.UNAVAILABLE)
class UsbStatus(J.Base, unittest.TestCase):
    """One simulator per test over the synthetic package set (test_sim_mod_job.SyntheticJob's seed)."""
    base = 'modbase'
    unverified = True
    setUpClass = classmethod(J.SyntheticJob.setUpClass.__func__)

    def usb(self):
        """The logged changes in order: ('mark', 'on'|'off') and ('line', text or None)."""
        log = (self.dir / 'log.txt').read_text(errors='replace') if (self.dir / 'log.txt').exists() else ''
        out = []
        for line in log.splitlines():
            m = re.search(r'menu: usb (mark|line):? (.*) at \d+ ms$', line)
            if m:
                out.append((m.group(1), None if m.group(2) == 'none' else m.group(2)))
        return out

    def until(self, cond, what, timeout=10.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if cond(self.usb()):
                return self.usb()
            time.sleep(0.02)
        self.fail('%s not logged: %s' % (what, self.usb()))

    def lines(self):
        return [v for k, v in self.usb() if k == 'line']

    def test_mark_with_frames_and_silence(self):
        """The first frame (setUp's HELLO) lights the mark; status calls keep it on; silence past host_seen_ms
        puts it out; the next frame lights it again."""
        self.until(lambda u: ('mark', 'on') in u, 'the mark after HELLO')
        for _ in range(5):
            self.assertEqual(self.dev.call(P.STATUS, b'')[0], 0)
            self.sim.advance(200)
        self.assertEqual([v for k, v in self.usb() if k == 'mark'], ['on'])
        self.sim.advance(SEEN_MS - 1500)                                     # well inside the window: still on
        self.assertEqual([v for k, v in self.usb() if k == 'mark'], ['on'])
        self.sim.advance(2500)
        self.until(lambda u: ('mark', 'off') in u, 'the mark out after silence')
        self.assertEqual(self.dev.call(P.STATUS, b'')[0], 0)
        self.until(lambda u: [v for k, v in u if k == 'mark'] == ['on', 'off', 'on'], 'the mark on again')
        self.assertEqual(self.lines(), [])                                   # reads and status: no activity line

    def test_line_during_a_staged_write(self):
        """stage.begin / data / end: "USB: receiving a file"; file.write_staged: "USB: writing files"; both
        logged before the write's reply, then gone host_activity_ms after the last request."""
        data = bytes(range(256)) * 16
        self.assertEqual(self.dev.stage(data), 0)
        self.assertIn('USB: receiving a file', self.lines())
        code, written, _ = self.dev.file_write(WRITE_PATH, hashlib.sha256(data).digest(), timeout=10.0)
        self.assertEqual((code, written), (0, len(data)))
        self.assertEqual(self.lines(), ['USB: receiving a file', 'USB: writing files'])
        self.assertEqual((self.root / WRITE_PATH[len('/yaffs2/'):]).read_bytes(), data)
        self.sim.advance(ACTIVITY_MS + 500)
        self.until(lambda u: [v for k, v in u if k == 'line'][-1:] == [None], 'the line gone')
        self.assertEqual(self.lines(), ['USB: receiving a file', 'USB: writing files', None])
        self.assertNotIn(('mark', 'off'), self.usb())                        # the host kept talking

    def test_line_during_a_usb_build(self):
        """mods.apply over USB: "USB: building Open-XY" while the job runs (job.status calls change nothing),
        gone once it ended and host_activity_ms passed."""
        job = self.start(['alpha'])
        self.assertIn('USB: building Open-XY', self.lines())
        f = self.finished(job)
        self.assertEqual(f['phase'], J.MS.DONE)
        self.sim.advance(ACTIVITY_MS + 500)
        self.until(lambda u: [v for k, v in u if k == 'line'][-1:] == [None], 'the line gone after the build')
        self.assertEqual([v for v in self.lines() if v], ['USB: building Open-XY'])


if __name__ == '__main__':
    unittest.main()
