"""Menu state machine + view on the host: actions and visible-row bounds."""
from pathlib import Path
import tempfile
import unittest

import ui_preview as P
from xy_forever import hostbuild

NONE, REDRAW, CONTINUE, SAFE_MODE, RESTORE, OPEN_MODS, CHANGE_SETTING, PICK_SETTING = range(8)   # xyf_menu Action
MAIN, INFO, HOME_EMPTY, SETTINGS, OPTIONS = range(5)            # xyf_menu Screen (#197: no confirm screens)


class UiFlowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='xyf-flow-')
        cls.lib = P.build(cls.tmp.name, run=hostbuild.run)
        cls.fb = P.framebuffer(cls.lib)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def drawn(self):
        self.lib.flow_draw(self.fb, b'status')
        hidden = sum(1 for c in range(P.WIDTH) for y in range(P.HEIGHT, P.STRIDE)
                     if self.fb[(P.WIDTH - 1 - c) * P.STRIDE + y])
        self.assertEqual(hidden, 0)

    def test_every_screen_draws_and_actions_flow(self):
        self.lib.flow_start(1); self.drawn()
        self.assertEqual(self.lib.flow_key(54), REDRAW)               # -> the safe-mode row
        self.assertEqual(self.lib.flow_key(51), REDRAW); self.drawn()  # no setup saved: the line says why
        self.lib.flow_safe(b'Boot without mods', 0)
        self.assertEqual((self.lib.flow_key(51), self.lib.flow_screen()), (SAFE_MODE, MAIN))   # #197: at once
        self.lib.flow_safe(b'Restore Open-XY (2 mods)', 1); self.drawn()
        self.assertEqual(self.lib.flow_key(51), RESTORE)
        self.lib.flow_safe(b'', 0)
        self.lib.flow_key(54)                                          # Mods
        self.assertEqual(self.lib.flow_key(51), OPEN_MODS)
        self.lib.flow_start(0); self.drawn()                            # built-in only
        self.assertEqual(self.lib.flow_key(51), CONTINUE)
        self.lib.flow_start(2); self.drawn()                            # nothing bootable: home
        self.assertEqual(self.lib.flow_key(51), REDRAW); self.drawn()   # 188: its one row, About (#21)


    def test_safe_mode_has_no_confirm_screen(self):
        """#197: Boot without mods acts on PLAY: no screen but the home menu is ever shown for it, whatever keys follow."""
        self.lib.flow_start(1)
        self.lib.flow_safe(b'Boot without mods', 0)
        self.lib.flow_key(54)                                          # the safe-mode row
        for key, action in ((51, SAFE_MODE), (54, REDRAW), (53, REDRAW), (51, SAFE_MODE), (52, NONE), (51, SAFE_MODE)):
            self.assertEqual((self.lib.flow_key(key), self.lib.flow_screen()), (action, MAIN), key)
        self.drawn()

    def test_home_menu_has_no_uninstall_row(self):
        """#197: four rows (Continue, the safe-mode row, Mods, Settings): + wraps after four, TRACK4 is Settings."""
        self.lib.flow_start(1)
        for _ in range(4): self.lib.flow_key(54)
        self.assertEqual(self.lib.flow_key(51), CONTINUE)              # wrapped back to Continue
        self.lib.flow_key(9)                                           # TRACK4
        self.assertEqual((self.lib.flow_key(51), self.lib.flow_screen()), (REDRAW, SETTINGS))

    def test_settings_page(self):
        """Settings, the last home row: - / + choose, PLAY acts (change_setting, entry = row), STOP back.
        Row 0 is Mod memory, its value and pending mark as xyf_mods_io sets them. Saves previews
        (settings-memory-standard.png, settings-memory-expanded-pending.png) in XYF_MEMORY_PREVIEW when set."""
        import os
        out = os.environ.get('XYF_MEMORY_PREVIEW')
        STANDARD, EXPANDED = 2, 3
        self.lib.flow_start(1)
        self.lib.flow_settings(STANDARD, 0, b'')
        for _ in range(3): self.lib.flow_key(54)                         # #197: Settings is row 3
        self.assertEqual(self.lib.flow_key(51), REDRAW)
        self.assertEqual(self.lib.flow_screen(), SETTINGS); self.drawn()
        if out:
            Path(out).mkdir(parents=True, exist_ok=True)
            P.png(Path(out) / 'settings-memory-standard.png', P.pixels(self.fb))
        self.assertEqual(self.lib.flow_key(51), CHANGE_SETTING)       # row 0: Mod memory
        self.assertEqual(self.lib.flow_key(54), REDRAW)
        self.lib.flow_settings(EXPANDED, 1, b''); self.drawn()
        if out:
            P.png(Path(out) / 'settings-memory-expanded-pending.png', P.pixels(self.fb))
        for memory in range(4):                                        # reading, unavailable, standard, expanded
            for pending in (0, 1):
                self.lib.flow_settings(memory, pending, b'These mod packages have no 4 MiB layout.' if memory == 1 else b'')
                self.drawn()
        self.assertEqual(self.lib.flow_key(52), REDRAW)               # STOP: back to the menu
        self.assertEqual(self.lib.flow_screen(), MAIN)

    def test_settings_rows_and_about(self):
        """#21: Menu at power-on, USB mode, About (the About page, back to Settings); every value draws. Saves
        previews (settings-rows.png, about.png) in XYF_SETTINGS_PREVIEW when set."""
        import os
        out = os.environ.get('XYF_SETTINGS_PREVIEW')

        def save(name):
            self.drawn()
            if out:
                Path(out).mkdir(parents=True, exist_ok=True)
                P.png(Path(out) / name, P.pixels(self.fb))
        self.lib.flow_start(1)
        self.lib.flow_settings(2, 0, b'')
        self.lib.flow_key(53)                                          # - : Settings, the last row
        self.assertEqual(self.lib.flow_key(51), REDRAW)
        self.assertEqual(self.lib.flow_screen(), SETTINGS)
        for power_on in range(2):
            for usb in range(3):
                for newer in (0, 1):
                    self.lib.flow_settings_rows(power_on, usb, newer); self.drawn()
        self.lib.flow_settings_rows(1, 1, 0)
        self.lib.flow_key(54)                                          # Menu at power-on
        save('settings-rows.png')
        self.assertEqual(self.lib.flow_key(51), CHANGE_SETTING)
        self.lib.flow_key(54)
        self.assertEqual(self.lib.flow_key(51), CHANGE_SETTING)       # USB mode
        self.lib.flow_key(54)
        self.assertEqual(self.lib.flow_key(51), CHANGE_SETTING)       # #65: Ask before USB control
        self.lib.flow_key(54)
        self.assertEqual(self.lib.flow_key(51), REDRAW)               # About
        self.assertEqual(self.lib.flow_screen(), INFO)
        save('about.png')
        self.assertEqual(self.lib.flow_key(52), REDRAW)               # STOP: back to Settings
        self.assertEqual(self.lib.flow_screen(), SETTINGS)
        # The option list (PLAY on USB mode: key_ui opens it), its moves, its pick; marked rows draw.
        self.lib.flow_key(53); self.lib.flow_key(53)
        self.assertEqual(self.lib.flow_key(51), CHANGE_SETTING)
        self.lib.flow_options(2); self.assertEqual(self.lib.flow_screen(), OPTIONS); save('options.png')
        self.assertEqual((self.lib.flow_key(54), self.lib.flow_option()), (REDRAW, 0))   # + wraps (three options)
        self.assertEqual(self.lib.flow_key(52), REDRAW)               # STOP: back, nothing picked
        self.assertEqual(self.lib.flow_screen(), SETTINGS)
        self.lib.flow_key(51); self.lib.flow_options(1); self.lib.flow_key(53)
        self.assertEqual((self.lib.flow_key(51), self.lib.flow_option(), self.lib.flow_screen()), (PICK_SETTING, 0, SETTINGS))
        for marks in (0, 2, 4, 6, 1, 7, 8, 15):
            self.lib.flow_settings_marked(marks); self.drawn()
        save('settings-unsaved.png')

if __name__ == '__main__':
    unittest.main()
