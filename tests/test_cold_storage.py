"""Cold reads stay out of L1 instruction SRAM (the readiness policy: tests/native/cold_storage_test.cpp)."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ColdStorageTests(unittest.TestCase):
    def test_cold_reads_avoid_l1_instruction_sram(self):
        # Every stalled cold test (002/005/006) CRC-read stock code at
        # 0x11a01d96 with core data loads; 003/004/007 did not. Keep L1
        # instruction SRAM out of identity spans and lookup readable ranges.
        def in_l1i(address):
            return 0x11a00000 <= address < 0x11a0c000
        storage = (ROOT / "target/cold_storage.cpp").read_text()
        body = storage[storage.index("bool identity()"):storage.index("bool media_guarded")]
        spans = [int(a, 16) for a in re.findall(r"^\s*\{(0x[0-9a-f]+)u,", body, re.M)]
        self.assertGreater(len(spans), 10)
        self.assertFalse([hex(a) for a in spans if in_l1i(a)])
        lookup = (ROOT / "target/retained_lookup.cpp").read_text()
        table = lookup[lookup.index("Range ranges["):lookup.index("const Memory memory")]
        starts = [int(a, 16) for a in re.findall(r"\{(0x[0-9a-f]+)u,", table)]
        self.assertGreater(len(starts), 3)
        self.assertFalse([hex(a) for a in starts if in_l1i(a)])


if __name__ == "__main__":
    unittest.main()
