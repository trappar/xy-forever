"""In the simulator: safe mode. The home menu's second row (where the Firmware library row was) is "Boot without
mods" while the saved configuration has mods on: PLAY (at once, #197: no confirmation) puts the base's official content in,
the base becomes the default, the record keeps the selection, built=, slot= and gains safe=1, and the base boots; a
plain power-on then boots it without writing. In safe mode the first row reads "<base> (safe mode)" and the second
"Restore Open-XY (N mods)": PLAY puts the Open-XY content back, makes the slot the default, safe=0, and boots it; a
slot that no longer holds what built= says is built again first. Each (and PLAY on a first row "(to apply)" or
"(rebuild)") is one converge sequence (#197) on the progress screen: the home row never moves, no key is dispatched,
no screen is drawn before it, the Mods page is never shown, and planning again gives nothing (the next PLAY writes
nothing). A mod build never takes the
saved slot. A content switch failure and a power cut in the switch: the record is the previous one and doing the same
again repairs it. With no setup saved the row is "Boot without mods" and PLAY only says why. Synthetic packages (test_sim_config). Needs
clang++ and the private stock 1.2.1 images (skipped without them). No device access."""
from pathlib import Path
import sys
import unittest

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
import test_sim_mod_job as J                                               # noqa: E402
from test_sim_config import  ConfigBase                                           # noqa: E402
from sim_mods_page import  PLAY                                     # noqa: E402

TRACK1, TRACK2 = 6, 7                                                      # the first row, the safe-mode row


@unittest.skipIf(J.UNAVAILABLE, J.UNAVAILABLE)
class SafeMode(ConfigBase, unittest.TestCase):
    def safe_row(self):
        """The safe-mode row's text as the last home-row line logged it ("-": none, no setup saved)."""
        return self.home().rpartition('; safe=')[2]

    def mark_now(self):
        self.mark = len(self.lines('menu: boots the default ')), len(self.lines('menu: save failed: '))

    def safe_mode(self):
        """The second row, PLAY: it converges at once (#197), no screen drawn in between (no confirmation)."""
        self.wait_log(lambda: self.safe_row() == 'Boot without mods', 'the safe-mode row')
        selected = len(self.lines('menu: screen 0 row 1 '))
        self.press(TRACK2)
        self.wait_log(lambda: len(self.lines('menu: screen 0 row 1 ')) > selected, 'the safe-mode row selected')
        screens, runs = len(self.lines('menu: screen ')), len(self.lines('menu: safe mode'))
        self.mark_now()
        self.key(PLAY, 1)                                                  # it boots: no release reaches B
        self.wait_log(lambda: len(self.lines('menu: safe mode')) > runs, 'Boot without mods')
        self.assertEqual(len(self.lines('menu: screen ')), screens)       # no confirm screen

    def restore(self, mods=1):
        self.wait_log(lambda: self.safe_row() == 'Restore Open-XY (%d mod%s)' % (mods, '' if mods == 1 else 's'),
                      'the restore row')
        self.press(TRACK2)
        self.mark_now()
        self.key(PLAY, 1)

    def in_safe_mode(self, a, mods=('alpha',)):
        c = self.saved_as(self.base, list(mods))                           # official content, the base the default
        self.assertEqual((c['safe'], c['slot']), ('1', a))
        return c

    def slots(self):
        """The Open-XY library entries' files (a build writes one)."""
        lib = self.entry('mods-a').parent.relative_to(self.root).as_posix() + '/mods-'
        return {k: v for k, v in self.snapshot().items() if k.startswith(lib)}


    # -- the tests ---------------------------------------------------------------------------------

    @pytest.mark.sweep
    def test_power_cut_in_the_safe_mode_switch_then_again(self):
        a = self.first_save()
        self.menu_again()
        self.sim.set('rename_fault_kind', 2)
        self.sim.set('rename_fault', 1)
        cuts = self.sim.status()['cuts']
        self.safe_mode()
        self.cut(cuts)
        self.assertEqual((self.config()['safe'], self.config()['mods']), ('0', 'alpha'))   # the record: as it was
        self.wait_log(lambda: not (self.root / 'xy-forever/job.txt').exists(), 'automatic content replay')
        self.wait_log(lambda: '(to apply) pending=1' in self.home(), 'the record to apply')
        self.safe_mode()                                                   # again: repaired
        self.assertEqual(self.booted(), self.base)
        self.in_safe_mode(a)

    def converged(self, action, row, steady_on=True):
        """The last `action` ('safe mode', 'restore', 'apply': its "menu: <action>" line) ran as one converge
        sequence (#197), up to its boot or failure: the progress screen from the first frame (the next line), the home
        row `row` all along (a job's rescan never moves it), no key reaching the menu or the Mods page (nothing
        dispatched), the Mods page never shown, and (steady_on) the toggles on never changed."""
        log = (self.dir / 'log.txt').read_text(errors='replace').splitlines()
        at = max(i for i, line in enumerate(log) if line.endswith('menu: ' + action))
        run = log[at:]
        end = next(i for i, line in enumerate(run) if 'menu: save failed: ' in line or 'menu: boots the default ' in line)
        run = run[:end + 1]
        self.assertIn('progress: job=', run[1])
        for marker in ('menu: screen ', 'menu: action ', 'menu: repeat key', 'progress: key ', 'mods page: '):
            self.assertEqual([line for line in run if marker in line], [], marker)
        shown = [dict(f.split('=', 1) for f in line[line.find('progress: job=') + 10:].partition(' title=')[0].split(' '))
                 for line in run if 'progress: job=' in line]
        self.assertEqual({(f['row'], f['screen'], f['mods']) for f in shown}, {(str(row), '0', '0')})
        if steady_on:
            self.assertEqual(len({f['on'] for f in shown}), 1, shown)
        return shown


if __name__ == '__main__':
    unittest.main()
