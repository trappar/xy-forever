"""XY Forever pages: host-rendered device C++ (list, confirm, progress, home)."""

import unittest

import ui_preview as P


class UiPagesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lib = P.build()
        cls.fb = P.framebuffer(cls.lib)

    def hidden_writes(self):
        return sum(
            1
            for col in range(P.WIDTH)
            for y in range(P.HEIGHT, P.STRIDE)
            if self.fb[(P.WIDTH - 1 - col) * P.STRIDE + y]
        )

    def test_scale_matches_integer_math(self):
        import random

        rng = random.Random(7)
        cases = [
            (448, 1300000, 2000000),
            (100, 3, 4),
            (448, 0, 5),
            (448, 5, 0),
            (480, 124000000, 124000000),
            (95, 7, 3),
            (0xFFFFFFFF, 2, 4),
            (448, 2303624, 3488536),
            (0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF),
            (0xFFFFFFFF, 0xFFFFFFFF, 1),
            (0x10000, 0x10000, 1),
            (0x8000FFFF, 0xFFFF8000, 0x7FFFFFFF),
        ]
        cases += [(rng.getrandbits(32), rng.getrandbits(32), rng.getrandbits(32) | 1) for _ in range(2000)]
        for a, b, c in cases:
            expected = 0 if c == 0 else min(a * b // c, 0xFFFFFFFF)
            self.assertEqual(self.lib.ui_scale(a, b, c), expected, (a, b, c))

    def test_list_scrolling_keeps_selection_visible(self):
        for count in range(16):
            for selected in range(count):
                first = self.lib.ui_list_first(count, selected)
                self.assertTrue(first <= selected < first + 7, (count, selected, first))
                self.assertLessEqual(first + min(count, 7), count)

    def test_pages_render_inside_visible_rows(self):
        renders = [
            lambda: self.lib.ui_render_library(self.fb, 6, 7),
            lambda: self.lib.ui_render_library(self.fb, 0, 0),
            lambda: self.lib.ui_render_confirm(self.fb, 0),
            lambda: self.lib.ui_render_confirm(self.fb, 1),
            lambda: self.lib.ui_render_progress(self.fb, 5, 0),
            lambda: self.lib.ui_render_progress(self.fb, 9, 4),
            lambda: self.lib.ui_render_home(self.fb, 1),
        ]
        for render in renders:
            render()
            self.assertEqual(self.hidden_writes(), 0)


if __name__ == '__main__':
    unittest.main()
