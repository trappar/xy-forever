"""The standalone stage-2 data audit (xy_forever/stage2_audit.py, #97): relocation pass, literal ranges and
the aligned-word scan, as a pure function over sections, relocations and literal ranges. The real hello and
menu images (P5) are audited by every --xyf-stage2 / --xyf-stage2-menu build and --verify."""
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from xy_forever import stage2_audit as A  # noqa: E402

BEGIN = A.STAGE2_WINDOW[0]
IMAGE = 0x800                       # image bytes: .text, then .rodata at +0x400 and .data at +0x600
RODATA, DATA = BEGIN + 0x400, BEGIN + 0x600
STOCK = 0x88a3477a                  # a stock 1.2.1 code address
FONT = next(iter(A.STAGE2_LITERAL_SYMBOLS))


def words(*values):
    return struct.pack('<%dI' % len(values), *values)


def pointer(address, target, kind=A.R_BFIN_BYTE4_DATA):
    return dict(offset=address, type=kind, target=target)


def audit(rodata=b'', data=b'', relocations=(), literals=()):
    sections = {'.rodata': dict(address=RODATA, data=rodata), '.data': dict(address=DATA, data=data)}
    relocated = {}
    for r in relocations:
        relocated.setdefault('.rodata' if r['offset'] < DATA else '.data', []).append(r)
    A.audit_stage2_data(sections, relocated, list(literals), IMAGE)


def symbols(font_bytes=475, strings=(RODATA, RODATA)):
    return {A.STAGE2_LITERALS[0]: dict(value=strings[0], size=0), A.STAGE2_LITERALS[1]: dict(value=strings[1], size=0),
            FONT: dict(value=RODATA + 0x20, size=font_bytes)}


class MustFail(unittest.TestCase):
    def test_n1_integer_table_of_a_stock_address(self):            # const uint32_t t[] = {0x88a3477a}
        with self.assertRaisesRegex(ValueError, 'foreign address 0x88a3477a'):
            audit(rodata=words(0, STOCK))

    def test_n2_function_pointer_cast_from_a_stock_address(self):  # no relocation: a plain word
        with self.assertRaisesRegex(ValueError, 'foreign address'):
            audit(data=words(STOCK))

    def test_n3_relocated_pointer_to_an_absolute_symbol(self):     # --defsym stock=0x88a3477a
        with self.assertRaisesRegex(ValueError, 'points outside itself'):
            audit(rodata=words(STOCK), relocations=[pointer(RODATA, STOCK)])

    def test_n4_relocated_pointer_past_the_image_end(self):
        past = BEGIN + IMAGE + 0x100
        self.assertFalse(any(lo <= past < hi for lo, hi in A.foreign_ranges(IMAGE)))   # the word scan misses it
        with self.assertRaisesRegex(ValueError, 'points outside itself'):
            audit(rodata=words(past), relocations=[pointer(RODATA, past)])

    def test_n5_relocation_inside_a_literal_range_or_a_resized_font(self):
        with self.assertRaisesRegex(ValueError, 'inside a literal range'):
            audit(rodata=words(0, BEGIN + 0x10), relocations=[pointer(RODATA + 4, BEGIN + 0x10)],
                  literals=[(RODATA, RODATA + 8)])
        sections = {'.rodata': dict(address=RODATA, data=bytes(0x200))}
        self.assertEqual(A.literal_ranges(symbols(), sections), [(RODATA, RODATA), (RODATA + 0x20, RODATA + 0x20 + 475)])
        with self.assertRaisesRegex(ValueError, 'is not 475 bytes'):
            A.literal_ranges(symbols(font_bytes=476), sections)
        with self.assertRaisesRegex(ValueError, 'outside .rodata'):
            A.literal_ranges(symbols(strings=(RODATA, RODATA + 0x201)), sections)

    def test_n6_aligned_uint16_pair(self):                         # const uint16_t t[] = {0x477a, 0x88a3}
        with self.assertRaisesRegex(ValueError, 'foreign address 0x88a3477a'):
            audit(rodata=struct.pack('<2H', 0x477a, 0x88a3))

    def test_relocations_are_four_byte_pointers_that_match_their_word(self):
        for kind, word in ((0x11, BEGIN + 4), (A.R_BFIN_BYTE4_DATA, BEGIN + 8)):
            with self.assertRaisesRegex(ValueError, 'not a 4-byte pointer'):
                audit(rodata=words(word), relocations=[pointer(RODATA, BEGIN + 4, kind)])
        with self.assertRaisesRegex(ValueError, 'relocates data in'):
            A.audit_stage2_data({'.rodata': dict(address=RODATA, data=b'')}, {'.bss': [pointer(RODATA, BEGIN)]}, [], IMAGE)


class MustPass(unittest.TestCase):
    def test_p1_merged_string_literal(self):
        text = b'e 2 from yaffs2/\0\0\0\0'                          # word 0x20322065, as in "Stage 2 is running."
        with self.assertRaisesRegex(ValueError, 'foreign address 0x20322065'):
            audit(rodata=text)
        audit(rodata=text, literals=[(RODATA, RODATA + len(text))])

    def test_p2_the_font(self):
        font = bytes(0x20) + words(0x2a7f2a24) * 118 + bytes(3)      # 475 bytes holding the bitmap word
        literals = A.literal_ranges(symbols(), {'.rodata': dict(address=RODATA, data=font)})
        audit(rodata=font, literals=literals)
        with self.assertRaises(ValueError):
            audit(rodata=font)

    def test_p3_pointer_table_of_its_own_strings(self):           # const char* const[] = {"a", "b"}
        strings = b'one\0two\0'
        table = words(RODATA, RODATA + 4)
        audit(rodata=strings + table, literals=[(RODATA, RODATA + 8)],
              relocations=[pointer(RODATA + 8, RODATA), pointer(RODATA + 12, RODATA + 4)])

    def test_p4_switch_jump_table(self):
        targets = (BEGIN + 0x11a, BEGIN + 0xbe, BEGIN + 0x152)
        audit(rodata=words(*targets), data=words(7, 0xffff),
              relocations=[pointer(RODATA + 4 * i, t) for i, t in enumerate(targets)])

    def test_unaligned_neighbours_are_still_scanned(self):
        # A word only partly literal is scanned; one spanning two adjacent ranges is not.
        with self.assertRaises(ValueError):
            audit(rodata=words(STOCK), literals=[(RODATA, RODATA + 3)])
        audit(rodata=words(STOCK), literals=[(RODATA, RODATA + 2), (RODATA + 2, RODATA + 4)])


if __name__ == '__main__':
    unittest.main()
