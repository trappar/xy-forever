"""Stage-2 file format: host packer vs device validator (same C++ as the device)."""
import ctypes as C
import hashlib
from pathlib import Path
import re
import struct
import sys
import tempfile
import unittest
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from xy_forever import hostbuild  # noqa: E402
from xy_forever import xyf_stage2 as S

OK, SIZE, MAGIC, ABI, HEADER_CRC, FLAGS, WINDOW, ENTRY, IMAGE_CRC, STAGE1, BUILD_ID, IMAGE_SHA = range(12)
BEGIN, END = 0x8ab90000, 0x8ac00000


def repack(data, image):
    """Rewrite header CRC fields for a changed image but keep the stated SHA."""
    head = bytearray(data[:92])
    struct.pack_into('<II', head, 24, len(image), zlib.crc32(image))
    return bytes(head) + struct.pack('<I', zlib.crc32(bytes(head))) + image


class Stage2Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='xyf-stage2-')
        lib = Path(cls.tmp.name) / 'stage2.dylib'
        hostbuild.run(['/usr/bin/clang++', '-std=c++11', '-O1', '-shared', '-fPIC', '-Wall', '-Wextra', '-Werror',
                       '-ffreestanding', '-fno-builtin', str(ROOT / 'target/xyf_stage2.cpp'),
                       str(ROOT / 'target/xyf_crc32.cpp'), str(ROOT / 'native/hash.cpp'),
                       str(ROOT / 'tests/xyf_stage2_bridge.cpp'), '-o', str(lib)], check=True, capture_output=True)
        cls.lib = C.CDLL(str(lib))
        for name in ('stage2_validate', 'stage2_accept'):
            getattr(cls.lib, name).argtypes = [C.c_char_p, C.c_uint, C.c_uint]
        cls.lib.stage2_constant.restype = C.c_uint
        cls.lib.stage2_path.restype = C.c_char_p

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def check(self, data, stage1=1):
        return self.lib.stage2_validate(data, len(data), stage1)

    def accept(self, data, stage1=1):
        return self.lib.stage2_accept(data, len(data), stage1)

    def test_round_trip_and_rejections(self):
        image = bytes(range(256)) * 64
        good = S.pack(image, BEGIN, BEGIN + 0x100, version=3, build_id='xyf-0.2')
        self.assertEqual(self.check(good), OK)
        self.assertEqual(self.accept(good), OK)
        self.assertEqual(S.inspect(good)['build_id'], 'xyf-0.2')
        self.assertEqual(self.check(good[:-1]), SIZE)
        self.assertEqual(self.check(b'XYS3' + good[4:]), MAGIC)
        flipped = bytearray(good); flipped[20] ^= 1                       # header field change -> header CRC
        self.assertEqual(self.check(bytes(flipped)), HEADER_CRC)
        flipped = bytearray(good); flipped[200] ^= 1                      # image byte -> image CRC
        self.assertEqual(self.check(bytes(flipped)), IMAGE_CRC)
        self.assertEqual(self.check(S.pack(image, BEGIN, BEGIN + 0x100, 3, 'x', min_stage1=5), stage1=4), STAGE1)
        for args in ((0x8b000000, 0x8b000100), (0x8a000000, 0x8a000100), (END - 0x100, END - 0x100),
                     (BEGIN + 2, BEGIN + 4)):
            with self.assertRaises(ValueError):
                S.pack(image, *args, 1, 'x')
        with self.assertRaises(ValueError):
            S.pack(image, BEGIN, BEGIN + len(image), 1, 'x')    # entry past the image
        with self.assertRaises(ValueError):
            S.inspect(bytes(flipped))

    def test_sha_is_checked_by_accept_only(self):
        image = bytes(range(256)) * 16
        good = S.pack(image, BEGIN, BEGIN, 1, 'hello')
        other = bytes(reversed(image))
        forged = repack(good, other)                                     # CRCs fine, SHA stale
        self.assertEqual(self.check(forged), OK)
        self.assertEqual(self.accept(forged), IMAGE_SHA)

    def test_window_edges(self):
        image = b'\0\0' * 8
        self.assertEqual(self.accept(S.pack(image, END - len(image), END - len(image), 1, 'edge')), OK)
        # A header that claims an address below the window (packed by hand: pack() refuses it).
        good = bytearray(S.pack(image, BEGIN, BEGIN, 1, 'low'))
        for load, expect in ((0x8b000000, WINDOW), (0x8ab10000, WINDOW), (0x8ab80000, WINDOW), (BEGIN - 4, WINDOW)):
            struct.pack_into('<II', good, 16, load, load)
            struct.pack_into('<I', good, 92, zlib.crc32(bytes(good[:92])))
            self.assertEqual(self.check(bytes(good)), expect, hex(load))

    def test_constants_match_host_tool_and_layout(self):
        v = [self.lib.stage2_constant(i) for i in range(10)]
        self.assertEqual((v[0], v[1]), S.WINDOW)
        self.assertEqual((v[0], v[1]), (BEGIN, END))
        self.assertEqual(v[2], END - BEGIN)
        self.assertEqual((v[3], v[4], v[5]), (S.HEADER_BYTES, S.ABI, 1))
        self.assertEqual((v[6], v[7], v[8], v[9]), (0, 1, 0x10000, 0xffffffff))
        self.assertEqual(self.lib.stage2_path().decode(), S.PATH)
        # Window lies in the module's 1 MiB region, above the stack top, clear of every other buffer.
        self.assertEqual(BEGIN >> 20, 0x8ab00000 >> 20)
        self.assertEqual((END - 1) >> 20, 0x8ab00000 >> 20)
        for lo, hi in ((0x88a65bf8, 0x8aa65bf4), (0x8a000000, 0x8a800000), (0x8ab00000, 0x8ab84000),
                       (0x8b000000, 0x8b800000), (0x8b800000, 0x8c000000), (0x8c000000, 0x8c03c000)):
            self.assertTrue(END <= lo or BEGIN >= hi, (hex(lo), hex(hi)))
        header = (ROOT / 'target/xyf_stage2.hpp').read_text()
        self.assertIn('sizeof(Services) == 20 && sizeof(Abi) == 32', header)

    def test_sha_digest_matches_hashlib(self):
        image = b'stage two' * 100
        data = S.pack(image, BEGIN, BEGIN, 1, 'h')
        self.assertEqual(data[32:64], hashlib.sha256(image).digest())
        self.assertTrue(re.fullmatch(r'[0-9a-f]{64}', S.inspect(data)['sha256']))


if __name__ == '__main__':
    unittest.main()
