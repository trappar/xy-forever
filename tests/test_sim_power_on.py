"""In the simulator: converging at power-on (#197, step B). Every convergence stores /yaffs2/xy-forever/converged.txt:
the SHA-256 of the selected configuration record and the default it converged to. A plain power-on whose fingerprint
matches, with nothing in doubt, boots the default with no menu, no progress screen, no USB and no write, as fast as a
plain boot ever did. Saved inputs changed and then the power lost (a flip saved, never applied), a fingerprint missing
(the first boot after an install: the converge reads the real state, so a slot that no longer holds what the record
says is built again) and an open journal each make the plain power-on converge on the progress screen first (the menu's
own journal replay included), store the fingerprint and boot. PLAY held at power-on only opens the menu. Synthetic
packages (test_sim_config). Needs clang++ and the private stock 1.2.1 images (skipped without them). No device access."""
from pathlib import Path
import sys
import unittest

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
import test_sim_mod_job as J                                               # noqa: E402
from test_sim_config import   ConfigBase                                                      # noqa: E402
from test_sim_job_recovery import intent, JOURNAL                          # noqa: E402
from xy_forever import bounds                                            # noqa: E402

CONVERGE = 'menu: power-on converge: doubt '                               # sim/devices.cpp, as key_ui.cpp


@unittest.skipIf(J.UNAVAILABLE, J.UNAVAILABLE)
class PowerOn(ConfigBase, unittest.TestCase):
    def counts(self):
        return {k: len(self.lines(k)) for k in (CONVERGE, 'menu (home)', 'progress: job=', 'menu: boots the default ')}

    def plain(self):
        """A power cycle without PLAY; returns the counts before it and the device time it took to reach the OS."""
        before = self.counts()
        self.mark = len(self.lines('menu: boots the default ')), len(self.lines('menu: save failed: '))
        status = self.sim.status()
        ons, start, link = status['power_ons'], status['clock_ms'], (status['to_a'], status['to_b'])
        self.sim.control('power plain')

        def booted():
            s = self.sim.status()
            return s['power_ons'] > ons and s['a'] == 'os' and s['b'] == 'os'
        self.wait_log(booted, 'both in the OS', bounds.WAIT_S)
        status = self.sim.status()
        self.messages = status['to_a'] - link[0], status['to_b'] - link[1]   # link messages relayed for the boot
        return before, status['clock_ms'] - start

    def converged(self, before, doubt):
        """The plain power-on converged on the progress screen (never the home menu's rows), then booted: its entry."""
        self.assertEqual(self.lines(CONVERGE)[before[CONVERGE]:], [str(doubt)])
        shown = self.progress()[before['progress: job=']:]
        self.assertTrue(shown, 'no progress screen')
        self.assertEqual({(p['title'], p['screen'], p['row'], p['mods']) for p in shown},
                         {('Checking the saved setup', 0, 0, 0)})
        self.assertEqual(len(self.lines('menu: boots the default ')), before['menu: boots the default '] + 1)
        return self.lines('menu: boots the default ')[-1]

    # -- the tests ---------------------------------------------------------------------------------

    @pytest.mark.sim
    def test_an_open_job_converges_through_the_journal_replay(self):
        """Kept: Power-on with an open job converges through the replay."""
        a = self.first_save()
        (self.root / JOURNAL).write_bytes(intent(self.root, a, self.base))   # a switch to a, cut before its cleanup
        before, _ = self.plain()
        self.assertEqual(self.converged(before, 2), a)
        self.assertFalse((self.root / JOURNAL).exists())
        self.assertFalse((self.root / 'xy-forever/job.old').exists())
        self.saved_as(a, ['alpha'])
        self.plain_boots_without_writing(a)

if __name__ == '__main__':
    unittest.main()
