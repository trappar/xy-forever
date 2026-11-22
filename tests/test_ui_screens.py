"""XY Forever screens: host-rendered with the device C++ (no device execution)."""
from pathlib import Path
import re
import unittest

import ui_preview as P
from xy_forever.source_text import unit_text

ROOT = Path(__file__).resolve().parents[1]


class UiScreensTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lib = P.build()
        cls.fb = P.framebuffer(cls.lib)

    def visible(self):
        """(x, y) of every non-black pixel, plus any write in the hidden rows 216..255."""
        lit, hidden = [], 0
        for col in range(P.WIDTH):
            base = (P.WIDTH - 1 - col) * P.STRIDE
            for y in range(P.STRIDE):
                if self.fb[base + y]:
                    if y < P.HEIGHT: lit.append((col, y))
                    else: hidden += 1
        return lit, hidden

    def test_framebuffer_matches_stock_layout(self):
        self.assertEqual(self.lib.ui_framebuffer_halfwords() * 2, 0x3c000)   # stock flush range
        self.lib.ui_render_text(self.fb, 0, 0, b'.', 1)                     # '.' = column 2, rows 5-6
        lit, _ = self.visible()
        self.assertEqual(sorted(lit), [(2, 5), (2, 6), (3, 5), (3, 6)])
        self.assertEqual(self.fb[(P.WIDTH - 1 - 2) * P.STRIDE + 5], 0xffff)  # stock 889ee89a mapping

    def test_menu_selection_bar_moves_and_stays_on_screen(self):
        bars = []
        for selected in range(4):                                          # #197: four home rows
            self.lib.ui_render_menu(self.fb, selected, b'')
            lit, hidden = self.visible()
            self.assertEqual(hidden, 0)
            self.assertTrue(lit)
            white_rows = {y for x, y in lit if x == 12 and self.fb[(P.WIDTH - 1 - x) * P.STRIDE + y] == 0xffff}
            bars.append(min(white_rows))
        self.assertEqual(bars, sorted(bars))
        self.assertEqual(len(set(bars)), 4)

    def test_info_screen_renders(self):
        self.lib.ui_render_message(self.fb)
        lit, hidden = self.visible()
        self.assertGreater(len(lit), 1000)
        self.assertEqual(hidden, 0)

    def lit_box(self, x0, x1, y0, y1, color=None):
        """(left, right, top, bottom) of the lit pixels (of color, if given) in [x0, x1) x [y0, y1), or None."""
        pts = [(x, y) for x in range(x0, x1) for y in range(y0, y1)
               if self.fb[(P.WIDTH - 1 - x) * P.STRIDE + y] and (color is None or self.fb[(P.WIDTH - 1 - x) * P.STRIDE + y] == color)]
        if not pts:
            return None
        return min(p[0] for p in pts), max(p[0] for p in pts), min(p[1] for p in pts), max(p[1] for p in pts)

    def test_logo_parts_line_up(self):
        """8.7: the infinity mark, "XY" and "FOREVER" in one 35 px band from y (the mark centred on the cap
        height), one baseline, the mark's left edge at x, 10 px between the parts."""
        ACCENT, WHITE = 0xfd20, 0xffff
        self.lib.ui_render_logo(self.fb, 20, 11)
        xy = self.lit_box(0, P.WIDTH, 0, 80, WHITE)
        ring = self.lit_box(0, xy[0], 0, 80, ACCENT)
        forever = self.lit_box(xy[1] + 1, P.WIDTH, 0, 80, ACCENT)
        self.assertEqual((ring[0], ring[2], ring[3]), (20, 11, 45))
        self.assertEqual((xy[2], xy[3]), (11, 45))                          # cap height 35
        self.assertEqual(forever[3], 45)                                    # the same baseline
        self.assertEqual((xy[0] - ring[1] - 1, forever[0] - xy[1] - 1), (10, 10))

    def test_home_chrome(self):
        """8.7: the USB mark top right only while lit (188: and the MTP mark left of it); nothing for storage OK;
        188: the activity right-aligned under the marks, the status line kept; the build on the hint line, right-aligned; on a compact page the mark in the title bar and
        the activity in place of the hint."""
        ACCENT, STATUS, HINT = 0xfd20, (184, 196), (200, P.HEIGHT)
        try:
            self.lib.ui_chrome(b'build 186', 0, b'')
            self.lib.ui_render_menu(self.fb, 0, b'')
            self.assertIsNone(self.lit_box(300, P.WIDTH, 0, 56))            # no version text, no mark
            self.assertIsNone(self.lit_box(0, P.WIDTH, *STATUS))             # storage OK: no status line
            self.assertIsNone(self.lit_box(0, P.WIDTH, 46, 56))              # 10 px from the logo to the rule
            build = self.lit_box(300, P.WIDTH, *HINT)
            self.assertEqual((build[1], build[1] - build[0] + 1), (P.WIDTH - 17, len('build 186') * 6 - 1))
            self.lib.ui_chrome(b'build 186', 1, b'')
            self.lib.ui_render_menu(self.fb, 0, b'')
            self.assertEqual(self.lit_box(300, P.WIDTH, 0, 56, ACCENT), (P.WIDTH - 16 - 23, P.WIDTH - 17, 11, 21))
            self.lib.ui_render_menu(self.fb, 0, b'Storage link lost.')
            status = self.lit_box(0, P.WIDTH, *STATUS, ACCENT)
            self.assertEqual(status[0], 16)
            self.lib.ui_chrome(b'build 186', 1, b'USB: installing XY Forever')
            self.lib.ui_render_menu(self.fb, 0, b'Storage link lost.')
            self.assertEqual(self.lit_box(0, P.WIDTH, *STATUS, ACCENT)[:2], status[:2])   # 188: status stays
            n = len('USB: installing XY Forever') * 6 - 1                # 188: the activity right-aligned under the marks
            self.assertEqual(self.lit_box(250, P.WIDTH, 24, 40, ACCENT), (P.WIDTH - 16 - n, P.WIDTH - 17, 27, 34))   # "g" descends to 34
            self.assertLess(self.lit_box(0, 300, 0, 56)[1], P.WIDTH - 16 - len("USB: uninstalling XY Forever") * 6 - 4)   # the longest clears the logo
            self.lib.ui_chrome(b'build 188', 3, b'MTP: transfer')     # 188: both marks lit, MTP left of USB
            self.lib.ui_render_menu(self.fb, 0, b'')
            self.assertEqual(self.lit_box(300, P.WIDTH, 0, 22, ACCENT), (P.WIDTH - 16 - 49, P.WIDTH - 17, 11, 21))
            self.assertEqual(self.lit_box(300, P.WIDTH - 16 - 26, 0, 22, ACCENT)[1], P.WIDTH - 16 - 27)   # 3 px apart
            self.lib.ui_chrome(b'build 188', 2, b'')                  # MTP alone: its own place, USB's empty
            self.lib.ui_render_menu(self.fb, 0, b'')
            self.assertEqual(self.lit_box(300, P.WIDTH, 0, 22, ACCENT), (P.WIDTH - 16 - 49, P.WIDTH - 16 - 27, 11, 21))
            self.lib.ui_chrome(b'build 186', 1, b'USB: installing XY Forever')
            self.lib.ui_render_library(self.fb, 0, 3)
            self.assertEqual(self.lit_box(300, P.WIDTH, 0, 24, ACCENT), (P.WIDTH - 16 - 23, P.WIDTH - 17, 7, 17))
            self.assertEqual(self.lit_box(0, P.WIDTH, *HINT, ACCENT)[:2], (16, 16 + n - 1))
        finally:
            self.lib.ui_chrome(b'build 186', 0, b'')

    def test_device_strings_fit_the_screen(self):
        # 6 px per character per scale unit; message lines use scale 1.
        text = unit_text(ROOT / 'target/key_ui.cpp')        # draw_screen is in its part key_ui_display.ipp
        body = text[text.index('void draw_screen('):text.index('void flush_framebuffer(')]
        literals = re.findall(r'"((?:[^"\\\n]|\\.)*)"', body)
        self.assertIn('Connected to a computer (xyf_host.py)', literals)
        for line in literals:
            self.assertLessEqual(len(line) * 6 - 1, P.WIDTH - 2 * 16, line)


if __name__ == '__main__':
    unittest.main()
