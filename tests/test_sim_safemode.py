"""In the simulator: safe mode. The home menu's second row (where the Firmware library row was) is "Boot without
mods" while the saved configuration has mods on: PLAY asks (Cancel first), then the base's official content goes in,
the base becomes the default, the record keeps the selection, built=, slot= and gains safe=1, and the base boots; a
plain power-on then boots it without writing. In safe mode the first row reads "<base> (safe mode)" and the second
"Restore Open-XY (N mods)": PLAY puts the Open-XY content back, makes the slot the default, safe=0, with no rebuild
while the slot still holds what built= says, and boots it. A mod build never takes the saved slot. A content switch
failure and a power cut in the switch: the record is the previous one and doing the same again repairs it. With no
setup saved the row is "Boot without mods" and PLAY only says why. Synthetic packages (test_sim_config). Needs
clang++ and the private stock 1.2.1 images (skipped without them). No device access."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
import test_sim_mod_job as J                                               # noqa: E402
from test_sim_config import ConfigBase                                    # noqa: E402
from test_sim_mods_page import PLAY, PLUS                                 # noqa: E402

CONFIRM_SAFE = 1                                                           # xyf_menu Screen::confirm_safe
TRACK2 = 7                                                                 # the safe-mode row


@unittest.skipIf(J.UNAVAILABLE, J.UNAVAILABLE)
class SafeMode(ConfigBase, unittest.TestCase):
    def safe_row(self):
        """The safe-mode row's text as the last home-row line logged it ("-": none, no setup saved)."""
        return self.home().rpartition('; safe=')[2]

    def mark_now(self):
        self.mark = len(self.lines('menu: boots the default ')), len(self.lines('menu: save failed: '))

    def safe_mode(self, accept=True):
        """The second row, PLAY, then Boot (accept) or Cancel."""
        self.wait_log(lambda: self.safe_row() == 'Boot without mods', 'the safe-mode row')
        self.press(TRACK2)
        before = len(self.lines('menu: screen %d ' % CONFIRM_SAFE))
        self.press(PLAY)
        self.wait_log(lambda: len(self.lines('menu: screen %d ' % CONFIRM_SAFE)) > before, 'the confirmation')
        self.mark_now()
        if not accept:
            self.press(PLAY)
            return
        self.press(PLUS)
        self.key(PLAY, 1)                                                  # it boots: no release reaches B

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

    def safe_saved(self):
        """alpha saved, then safe mode: the slot."""
        a = self.first_save()
        self.menu_again()
        self.safe_mode()
        self.assertEqual(self.booted(), self.base)
        self.in_safe_mode(a)
        self.menu_again()
        return a

    # -- the tests ---------------------------------------------------------------------------------
    def test_no_setup_the_row_says_why(self):
        self.wait_log(lambda: 'pending=0' in self.home(), 'the home row')
        self.assertEqual(self.safe_row(), '-')
        before = self.snapshot()
        self.press(TRACK2)
        self.press(PLAY)
        self.wait_log(lambda: self.lines('menu: line: No mods are saved'), 'the line')
        self.assertEqual(self.lines('menu: screen %d ' % CONFIRM_SAFE), [])
        self.assertEqual(self.snapshot(), before)

    def test_safe_mode_power_on_and_restore(self):
        a = self.first_save()
        self.menu_again()
        before = self.snapshot()
        self.safe_mode(accept=False)                                       # Cancel: nothing changes
        self.wait_log(lambda: self.lines('menu: screen 0 '), 'home')
        self.assertEqual(self.snapshot(), before)
        record = self.config()
        self.safe_mode()
        self.assertEqual(self.booted(), self.base)
        c = self.in_safe_mode(a)
        self.assertEqual({k: v for k, v in c.items() if k != 'safe'}, {k: v for k, v in record.items() if k != 'safe'})
        self.plain_boots_without_writing(self.base)
        self.menu_again()
        self.wait_log(lambda: self.home().startswith('Base (safe mode) pending=0'), 'the safe-mode home row')
        since = len(self.pages())
        self.restore()
        self.assertEqual(self.booted(), a)                                 # Restore boots Open-XY, as Save does
        self.assertFalse(self.built_any(since))                            # the slot still holds built=
        c = self.saved_as(a, ['alpha'])
        self.assertEqual(c['safe'], '0')
        self.menu_again()
        self.wait_log(lambda: self.home().startswith('Open-XY - 1 mod - 1.2.1 pending=0'), 'Open-XY again')
        self.assertEqual(self.safe_row(), 'Boot without mods')

    def test_a_build_never_takes_the_saved_slot(self):
        """In safe mode the default is the base: both mods slots would be free, but builds go to the other one."""
        a = self.safe_saved()
        stamp = (self.entry(a) / 'info.txt').read_bytes()
        other = {'mods-a': 'mods-b', 'mods-b': 'mods-a'}[a]
        self.assertEqual(self.build(['beta']), other)
        self.assertEqual(self.build(['gamma']), other)
        self.assertEqual((self.entry(a) / 'info.txt').read_bytes(), stamp)

    def test_safe_mode_switch_failure_then_again_repairs(self):
        a = self.first_save()
        self.menu_again()
        record = (self.root / 'xy-forever/config.txt').read_bytes()
        self.sim.set('rename_fault_kind', 1)
        self.sim.set('rename_fault', 1)                                    # the switch's first rename
        self.safe_mode()
        self.assertIn('Boot without mods again', self.failed())
        self.assertEqual(((self.root / 'xy-forever/config.txt').read_bytes(), self.disk_default()), (record, a))
        self.sim.set('rename_fault', 0)
        self.safe_mode()
        self.assertEqual(self.booted(), self.base)
        self.in_safe_mode(a)

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

    def test_restore_failure_then_again(self):
        a = self.safe_saved()
        record = (self.root / 'xy-forever/config.txt').read_bytes()
        self.sim.set('rename_fault_kind', 1)
        self.sim.set('rename_fault', 1)
        self.restore()
        self.assertIn('Restore Open-XY again', self.failed())
        self.assertEqual(((self.root / 'xy-forever/config.txt').read_bytes(), self.disk_default()), (record, self.base))
        self.sim.set('rename_fault', 0)
        self.restore()
        self.assertEqual(self.booted(), a)
        self.assertEqual(self.saved_as(a, ['alpha'])['safe'], '0')


if __name__ == '__main__':
    unittest.main()
