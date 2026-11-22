"""xyf_clock: the cold code's millisecond clock is exact and wrap-safe (no 524 s overflow)."""
from pathlib import Path
import ctypes
import random
import tempfile
import unittest

from xy_forever import hostbuild

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = r"""
#include "xyf_clock.hpp"
extern "C" unsigned clock_ms(unsigned low, unsigned high) { return xyf::clock::ms_from_cycles(low, high); }
extern "C" unsigned clock_units(unsigned units) { return xyf::clock::ms_from_units(units); }
"""


def exact(cycles):
    return ((cycles >> 10) * 21 >> 13) & 0xffffffff


class ClockTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='xyf-clock-')
        src = Path(cls.tmp.name) / 'bridge.cpp'
        src.write_text(BRIDGE)
        lib = Path(cls.tmp.name) / 'clock.dylib'
        hostbuild.run(['/usr/bin/clang++', '-std=c++11', '-Wall', '-Wextra', '-Werror', '-shared', '-fPIC',
                       '-I', str(ROOT / 'target'), str(src), '-o', str(lib)], check=True)
        cls.lib = ctypes.CDLL(str(lib))
        for f in (cls.lib.clock_ms, cls.lib.clock_units):
            f.restype = ctypes.c_uint
        cls.lib.clock_ms.argtypes = [ctypes.c_uint, ctypes.c_uint]
        cls.lib.clock_units.argtypes = [ctypes.c_uint]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def ms(self, cycles):
        return self.lib.clock_ms(cycles & 0xffffffff, cycles >> 32)

    def test_exact_against_big_integers(self):
        rng = random.Random(4231)
        points = [0, 1, 1023, 1024, 400000000, 524 * 400000000, 525 * 400000000, (1 << 32) - 1, 1 << 32,
                  (1 << 42) - 1, 1 << 42, 3 * 3600 * 400000000, 49 * 86400 * 400000000, (1 << 64) - 1]
        points += [rng.randrange(1 << 64) for _ in range(5000)]
        for cycles in points:
            self.assertEqual(self.ms(cycles), exact(cycles), hex(cycles))

    def test_differences_survive_the_old_overflow_and_wraps(self):
        # The old ((units * 21) >> 13) jumped back to 0 after ~524 s; intervals across it broke.
        second = 400000000
        for start in (0, 500 * second, 10000 * second, (1 << 42) - 7 * second, 1 << 63):
            for interval in (0, 250, 10 * second, 600 * second, 4 * 3600 * second, 30 * 86400 * second):
                got = (self.ms(start + interval) - self.ms(start)) & 0xffffffff
                self.assertAlmostEqual(got, interval * 21 / 8192 / 1024, delta=1.01)
        # Across the 2^32 ms wrap (~49.7 days at this scale) the unsigned difference still holds.
        wrap_cycles = ((1 << 32) * 8192 // 21) << 10
        a, b = self.ms(wrap_cycles - 5 * second), self.ms(wrap_cycles + 5 * second)
        self.assertGreater(a, b)
        self.assertAlmostEqual((b - a) & 0xffffffff, 10 * second * 21 / 8192 / 1024, delta=1.01)

    def test_units_form_has_no_overflow_for_three_hours(self):
        for units in (0, 8191, 8192, 204522252, 204522253, 0xffffffff):
            self.assertEqual(self.lib.clock_units(units), units * 21 >> 13)


if __name__ == '__main__':
    unittest.main()
