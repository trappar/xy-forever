"""Mod memory in the simulator: the Settings page's one row. Part of the saved configuration (config.txt
memory=), pending until boot like the Mods page's toggles: PLAY opens its options and a pick changes it, Continue
asks Save / Discard / Back, Save rebuilds Open-XY in the 4 MiB layout (mods.apply flag bit 1); a power-on keeps the mode; no change, no rebuild; back
to 2 MiB with a selection over 2032 KiB: Save refused with the limit text. The published 0.0.6 set (its base
has expanded_module_base); the synthetic set (none, as a set from before the 4 MiB option): the row unavailable, PLAY changes
nothing. Driven by injected keys (tests/test_sim_config.py's helpers). Needs clang++ and the private stock 1.2.1
images (skipped without them). No device access."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
import test_sim_mod_job as J                                               # noqa: E402
import test_sim_config as SC                                              # noqa: E402
from test_ui_mods import info                                             # noqa: E402
from test_sim_mods_page import synthetic_setup, PLAY, STOP, MINUS, PLUS    # noqa: E402
import test_sim_content_switch as CS                                      # noqa: E402

SETTINGS_ROW, SETTINGS = 4, 6                                              # xyf_menu item_settings (188), Screen::settings
STANDARD, EXPANDED = 'memory=2 MiB;', 'memory=4 MiB (experimental);'
EXPANDED_PENDING = 'memory=4 MiB (experimental): applied when you boot;'
STANDARD_PENDING = 'memory=2 MiB: applied when you boot;'
UNAVAILABLE = 'memory=Not available;'
TOO_BIG = 'note=Too big for the mods memory: turn some mods off.;'


class Memory(SC.Config):
    """Config's helpers (its tests are not run again here)."""

    def settings(self):
        """Home -> Settings (the Mod memory row); its packages are read there."""
        for _ in range(SETTINGS_ROW):
            self.press(PLUS)
        self.press(PLAY)
        self.wait_log(lambda: self.lines('menu: screen %d ' % SETTINGS), 'the Settings page', 10.0)

    def pick_other(self):
        """#21: PLAY opens Mod memory's options (on the value in effect), + moves to the other, PLAY picks it."""
        opened = len(self.lines('settings: options current'))
        self.press(PLAY)
        self.wait_log(lambda: len(self.lines('settings: options current')) > opened, 'the option list', 10.0)
        self.press(PLUS)
        self.press(PLAY)

    def home_has(self, text, timeout=60.0):
        self.wait_log(lambda: text in self.home(), 'home row with %r: %s' % (text, self.home()), timeout)
        return self.home()

    def back_to_continue(self):
        self.press(STOP)
        for _ in range(SETTINGS_ROW):
            self.press(MINUS)


for _name in [n for n in dir(SC.Config) if n.startswith('test_')]:
    setattr(Memory, _name, None)


@unittest.skipIf(J.UNAVAILABLE, J.UNAVAILABLE)
@unittest.skipUnless(J._published(), 'published-kit packages, stock os.ldr, op1_factory.json or content.sha256 absent')
class PublishedMemory(Memory, unittest.TestCase):
    base = 'opxy-121'
    unverified = False

    @classmethod
    def setUpClass(cls):
        J.PublishedJob.setUpClass.__func__(cls)
        # a saved selection over 2032 KiB (32 machines; tests/test_ui_mods.py PublishedPage.test_mod_memory), never
        # built: Save builds it
        machines = [f for f in sorted(p.name for p in cls.gen_dir.iterdir() if p.is_dir() and p.name != 'base')
                    if not f.startswith('ext-')][:32]
        cls.machines = sorted(machines, key=lambda f: int(info(cls.gen_dir / f)['order']))   # build order
        cls.official = CS.content(cls.seeded)                              # what saved_as compares content/ with
        (cls.seeded / SC.CONFIG).write_text('format=1\nbase=%s\nprofile=%s\ngeneration=%s\nunverified=0\nbuilt=\nslot=\n'
                                         'stamp=\nmemory=standard\nmods=%s\n' % (cls.base, cls.profile, cls.generation,
                                                                                 ','.join(machines)))

    def test_flip_save_power_on_no_change_and_back(self):
        self.settings()
        self.home_has(STANDARD)                                            # "Checking the mod packages..." until the base
        self.home_has(TOO_BIG)                                             # read on the Settings page: over 2032 KiB
        self.pick_other()                                                  # Mod memory -> 4 MiB, pending
        row = self.home_has(EXPANDED_PENDING)
        self.assertIn('pending=1 blocked=0', row)
        self.assertIn('note=;', row)                                       # fits the 4 MiB layout
        self.assertEqual(self.config()['memory'], 'standard')              # not saved at once
        self.back_to_continue()
        self.cont(0)                                                       # Save
        entry = self.booted(600.0)
        c = self.saved_as(entry, self.machines)
        self.assertEqual(c['memory'], 'expanded')
        self.assertTrue(c['built'].endswith(' 32 expanded'), c['built'])
        self.assertFalse((self.entry(entry) / 'layout.json').exists())
        self.assertTrue(any(p['save'] & 2 for p in self.pages() if 'save' in p))   # it built
        self.menu_again()                                                  # power-on: the mode kept
        row = self.home_has(EXPANDED)
        self.assertIn('pending=0', row)
        before, saves = self.snapshot(), len(self.lines('menu: save'))
        self.cont(dialog=False)                                            # no change: boots at once, no rebuild
        self.assertEqual(self.booted(), entry)
        self.assertEqual((len(self.lines('menu: save')), self.snapshot()), (saves, before))
        self.menu_again()
        self.home_has(EXPANDED)
        self.settings()
        self.pick_other()                                                  # back to 2 MiB: does not fit
        row = self.home_has(TOO_BIG)                                       # once the page's list is read
        self.assertIn('pending=1 blocked=1 ' + STANDARD_PENDING, row)
        self.back_to_continue()
        self.press(PLAY)
        self.wait_log(lambda: self.lines('menu: screen %d ' % SC.SAVE_DIALOG), 'the dialog')
        self.assertTrue(self.lines('menu: screen %d ' % SC.SAVE_DIALOG)[-1].endswith(' choice 1'))   # Discard first: no Save
        self.press(STOP)
        self.assertEqual(self.config()['memory'], 'expanded')              # nothing saved


@unittest.skipIf(J.UNAVAILABLE, J.UNAVAILABLE)
class OldPackagesMemory(Memory, unittest.TestCase):
    """The synthetic set: its base has no expanded_module_base (a set written before the 4 MiB option)."""
    base = 'modbase'
    unverified = True

    @classmethod
    def setUpClass(cls):
        synthetic_setup(cls)
        cls.seed_content()

    def test_row_unavailable_play_changes_nothing(self):
        self.wait_log(lambda: 'pending=0' in self.home(), 'the home row')
        self.settings()
        self.home_has(UNAVAILABLE)
        self.press(PLAY)
        self.wait_log(lambda: self.lines('settings: play on row 0: note='), 'the PLAY')
        self.assertEqual(self.lines('settings: play on row 0: note=')[-1], 'These mod packages have no 4 MiB layout.')
        self.assertIn('pending=0', self.home())
        self.assertIn(UNAVAILABLE, self.home())
        self.back_to_continue()
        self.cont(dialog=False)                                            # nothing pending: boots at once
        self.booted()
        self.assertIsNone(self.config())


if __name__ == '__main__':
    unittest.main()
