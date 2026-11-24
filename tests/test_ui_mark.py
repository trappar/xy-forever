"""The infinity mark's masks (#315, scripts/mark_mask.py): the generated target/ui_mark_*.ipp are current, and the
curve is a smooth, even-width stroke filling its box (no bump at the crossing)."""

import importlib.util
import unittest
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('mark_mask', ROOT / 'scripts/mark_mask.py')
M = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M)


class MarkMaskTest(unittest.TestCase):
    @pytest.mark.uses('scripts/mark_mask.py')
    def test_masks_are_current_and_fill_their_box(self):
        for name, (width, height, stroke) in M.MASKS.items():
            with self.subTest(name):
                self.assertEqual(M.path(name).read_text(), M.source(name), 'run: uv run scripts/mark_mask.py')
                rows = M.alpha(width, height, stroke)
                lit = [(i, j) for j, row in enumerate(rows) for i, a in enumerate(row) if a]
                self.assertEqual(min(i for i, _ in lit), 0)
                self.assertEqual(max(i for i, _ in lit), width - 1)
                self.assertEqual(min(j for _, j in lit), 0)
                self.assertEqual(max(j for _, j in lit), height - 1)
                # The crossing: one run down the middle column, centred (two strokes through one point).
                middle = [rows[j][width // 2] for j in range(height)]
                run = [j for j, a in enumerate(middle) if a]
                self.assertEqual(run, list(range(run[0], run[-1] + 1)))
                self.assertEqual(run[0] + run[-1], height - 1)
                # Symmetric left to right and top to bottom.
                self.assertEqual(rows, [row[::-1] for row in rows])
                self.assertEqual(rows, rows[::-1])


if __name__ == '__main__':
    unittest.main()
