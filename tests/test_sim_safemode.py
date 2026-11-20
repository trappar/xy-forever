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
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
import test_sim_mod_job as J                                               # noqa: E402
from test_sim_config import ConfigBase                                    # noqa: E402
from test_sim_mods_page import MINUS, PLAY, PLUS, STOP                   # noqa: E402

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

    def home_only(self, pages, slots):
        """Since `pages` "mods page:" lines: the Mods page was never shown, and nothing was built."""
        self.assertEqual(self.pages()[pages:], [])
        self.assertEqual(self.slots(), slots)

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
        self.assertEqual(self.lines('menu: safe mode'), [])
        self.assertEqual(self.snapshot(), before)

    def test_safe_mode_power_on_and_restore(self):
        a = self.first_save()
        self.menu_again()
        record = self.config()
        self.safe_mode()
        self.assertEqual(self.booted(), self.base)
        c = self.in_safe_mode(a)
        self.assertEqual({k: v for k, v in c.items() if k != 'safe'}, {k: v for k, v in record.items() if k != 'safe'})
        self.plain_boots_without_writing(self.base)
        self.menu_again()
        self.wait_log(lambda: self.home().startswith('Base (safe mode) pending=0'), 'the safe-mode home row')
        since = len(self.progress())
        self.restore()
        self.assertEqual(self.booted(), a)                                 # Restore boots Open-XY, as Continue does
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

    def again_writes_nothing(self, entry):
        """Planning again gives nothing: at the next menu nothing is pending, and PLAY boots `entry` writing nothing."""
        self.menu_again()
        self.wait_log(lambda: ' pending=0 ' in self.home(), 'nothing pending')
        before, sequences = self.snapshot(), len(self.lines('progress: job='))
        self.cont()
        self.assertEqual(self.booted(), entry)
        self.assertEqual((self.snapshot(), len(self.lines('progress: job='))), (before, sequences))

    def test_continue_converges_on_the_progress_screen(self):
        self.change(['alpha'])
        self.cont()                                                        # the flip written first, if not yet
        a = self.booted()
        self.converged('apply', 0)
        self.saved_as(a, ['alpha'])
        self.again_writes_nothing(a)

    def test_safe_restore_and_apply_converge_on_the_progress_screen(self):
        """Boot without mods, Restore, then PLAY on the first row "(to apply)" after a power cut in a switch: each runs
        on the progress screen alone, and planning again gives nothing."""
        a = self.first_save()
        self.menu_again()
        self.safe_mode()
        self.assertEqual(self.booted(), self.base)
        self.converged('safe mode', 1)
        self.in_safe_mode(a)
        self.again_writes_nothing(self.base)
        self.menu_again()
        self.restore()
        self.assertEqual(self.booted(), a)
        self.converged('restore', 1)
        self.assertEqual(self.saved_as(a, ['alpha'])['safe'], '0')
        self.again_writes_nothing(a)
        self.menu_again()
        self.sim.set('rename_fault_kind', 2)
        self.sim.set('rename_fault', 1)
        cuts = self.sim.status()['cuts']
        self.safe_mode()
        self.cut(cuts)                                                     # the record: Open-XY; the content: replayed
        self.wait_log(lambda: not (self.root / 'xy-forever/job.txt').exists(), 'automatic content replay')
        self.wait_log(lambda: '(to apply) pending=1' in self.home(), 'the record to apply')
        self.mark_now()
        self.cont()                                                        # PLAY on the first row: it applies
        self.assertEqual(self.booted(), a)
        self.converged('apply', 0)
        self.assertEqual(self.saved_as(a, ['alpha'])['safe'], '0')
        self.again_writes_nothing(a)

    def test_a_failed_safe_mode_changes_nothing(self):
        """A failed Boot without mods returns to the home menu with the reason: the Mods page was never shown, nothing
        was built, the saved selection and its toggles stay."""
        self.first_save()
        self.menu_again()
        slots, pages = self.slots(), len(self.pages())
        self.sim.set('rename_fault_kind', 1)
        self.sim.set('rename_fault', 1)                                    # the switch fails: back on the home menu
        self.safe_mode()
        self.assertIn('Boot without mods again', self.failed())
        self.converged('safe mode', 1)
        self.home_only(pages, slots)
        self.assertEqual(self.config()['mods'], 'alpha')
        self.press(TRACK1)                                                 # Continue, then the Mods row
        p = self.open_page()
        self.assertEqual((p['on'], p['changed']), (['alpha'], 0))

    def test_restore_builds_when_the_slot_changed(self):
        """The slot no longer holds what built= says: Restore builds the saved selection again (into the spare slot),
        then boots it, still on the progress screen alone."""
        a = self.safe_saved()
        info = self.entry(a) / 'info.txt'
        info.write_text(re.sub(r'sha256=\w+', 'sha256=' + '0' * 64, info.read_text()))
        since = len(self.progress())
        self.restore()
        b = self.booted()
        self.assertEqual(b, {'mods-a': 'mods-b', 'mods-b': 'mods-a'}[a])
        self.assertTrue(self.built_any(since))
        self.converged('restore', 1, steady_on=False)                      # the list is read for the build
        self.assertEqual(self.saved_as(b, ['alpha'])['safe'], '0')
        self.again_writes_nothing(b)

if __name__ == '__main__':
    unittest.main()
