"""Menu state machine + view on the host: actions and visible-row bounds."""
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import ui_preview as P  # noqa: E402
from xy_forever import hostbuild  # noqa: E402

NONE, REDRAW, CONTINUE, BOOT_ONCE, SET_DEFAULT, UNINSTALL = range(6)
CHANGE_SETTING = 9                                             # xyf_menu Action (build 188: no mtp_mode)


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
        self.assertEqual(self.lib.flow_key(54), REDRAW)               # -> library row
        self.assertEqual(self.lib.flow_key(51), REDRAW); self.drawn()  # library
        self.assertEqual(self.lib.flow_key(51), BOOT_ONCE)
        self.lib.flow_hold(55, 1)
        self.assertEqual(self.lib.flow_key(51), SET_DEFAULT)
        self.lib.flow_hold(55, 0)
        self.assertEqual(self.lib.flow_key(52), REDRAW)               # stop: back
        for _ in range(2): self.lib.flow_key(54)                       # Mods, Uninstall (188: no MTP row)
        self.assertEqual(self.lib.flow_key(51), REDRAW); self.drawn()  # confirm
        self.lib.flow_key(54)
        self.assertEqual(self.lib.flow_key(51), UNINSTALL)
        self.lib.flow_start(0); self.drawn()                            # built-in only
        self.assertEqual(self.lib.flow_key(51), CONTINUE)
        self.lib.flow_start(2); self.drawn()                            # nothing bootable: home
        self.assertEqual(self.lib.flow_key(51), REDRAW); self.drawn()   # 188: its one row, Info


    def test_settings_page(self):
        """Settings, the row above Info: - / + choose, PLAY acts (change_setting, entry = row), STOP back.
        The one row is Mod memory, its value and pending mark as xyf_mods_io sets them. Saves previews
        (settings-memory-standard.png, settings-memory-expanded-pending.png) in XYF_MEMORY_PREVIEW when set."""
        import os
        out = os.environ.get('XYF_MEMORY_PREVIEW')
        STANDARD, EXPANDED = 2, 3
        self.lib.flow_start(1)
        self.lib.flow_settings(STANDARD, 0, b'')
        for _ in range(4): self.lib.flow_key(54)                         # 188: Settings is row 4
        self.assertEqual(self.lib.flow_key(51), REDRAW)
        self.assertEqual(self.lib.flow_screen(), 6); self.drawn()
        if out:
            Path(out).mkdir(parents=True, exist_ok=True)
            P.png(Path(out) / 'settings-memory-standard.png', P.pixels(self.fb))
        self.assertEqual(self.lib.flow_key(51), CHANGE_SETTING)       # row 0: Mod memory (the only row)
        self.assertEqual(self.lib.flow_key(54), REDRAW)
        self.lib.flow_settings(EXPANDED, 1, b''); self.drawn()
        if out:
            P.png(Path(out) / 'settings-memory-expanded-pending.png', P.pixels(self.fb))
        for memory in range(4):                                        # reading, unavailable, standard, expanded
            for pending in (0, 1):
                self.lib.flow_settings(memory, pending, b'These mod packages have no 4 MiB layout.' if memory == 1 else b'')
                self.drawn()
        self.assertEqual(self.lib.flow_key(52), REDRAW)               # STOP: back to the menu
        self.assertEqual(self.lib.flow_screen(), 0)

if __name__ == '__main__':
    unittest.main()
