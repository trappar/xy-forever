"""Table CRC-32 matches zlib (host build of the device C++)."""

import ctypes as C
import random
import tempfile
import unittest
import zlib
from pathlib import Path

import pytest
import xyf_hosttest

from xy_forever import hostbuild
from xy_forever.paths import STOCK_121

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.gate
class Crc32Test(unittest.TestCase):
    def test_matches_zlib_including_chaining(self):
        crc = xyf_hosttest.module('crc32')
        for name in ('update', 'update_volatile'):
            f = getattr(crc, name)
            rng = random.Random(3)
            for size in (0, 1, 7, 255, 4096, 100003):
                data = bytes(rng.getrandbits(8) for _ in range(size))
                self.assertEqual(f(0, data), zlib.crc32(data), (name, size))
                split = size // 3
                self.assertEqual(f(f(0, data[:split]), data[split:]), zlib.crc32(data))
            self.assertEqual(f(0, b'123456789'), 0xCBF43926)
            # The cold adapter now shares this implementation for full images.
            # Check a real image, an unaligned slice, and corruption at each end.
            stock = STOCK_121 / 'os.ldr'
            image = stock.read_bytes() if stock.is_file() else bytes(range(256)) * 9500
            for sample in (image, image[1:-1], bytes([image[0] ^ 1]) + image[1:], image[:-1] + bytes([image[-1] ^ 1])):
                self.assertEqual(f(0, sample), zlib.crc32(sample))


if __name__ == '__main__':
    unittest.main()


@pytest.mark.gate
class WordCrcAlignmentTest(unittest.TestCase):
    """Both CRCs read aligned input a word at a time: every start offset and length must agree with zlib."""

    def test_every_alignment(self):
        crc = xyf_hosttest.module('crc32')
        functions = [lambda d, o: crc.update(0, d, o), lambda d, o: crc.update_volatile(0, d, o), crc.zip]
        # The narrow modes' nibble table (XYF_SMALL_CRC) is the same sources built with another define: the
        # extension builds each source once, so this one stays a separate host build.
        bridge = (
            '#include "../native/inflate.hpp"\n'
            'extern "C" unsigned zip_crc(const unsigned char* d, unsigned n) '
            '{ return xyf_core::zip::crc32_update(0xffffffffu, d, n) ^ 0xffffffffu; }\n'
        )
        with tempfile.TemporaryDirectory(prefix='xyf-crcw-') as tmp:
            (Path(tmp) / 'zip_crc.cpp').write_text(bridge.replace('../native/', str(ROOT / 'native') + '/'))
            small = Path(tmp) / 'crcs.dylib'
            hostbuild.run(
                [
                    '/usr/bin/clang++',
                    '-std=c++11',
                    '-O1',
                    '-shared',
                    '-fPIC',
                    '-Wall',
                    '-Wextra',
                    '-Werror',
                    '-ffreestanding',
                    '-fno-builtin',
                    '-DXYF_SMALL_CRC=1',
                    str(ROOT / 'native/inflate.cpp'),
                    str(ROOT / 'target/xyf_crc32.cpp'),
                    str(ROOT / 'tests/xyf_crc32_bridge.cpp'),
                    str(Path(tmp) / 'zip_crc.cpp'),
                    '-o',
                    str(small),
                ],
                check=True,
                capture_output=True,
            )
            small_crc = C.CDLL(str(small))
            buffer = C.create_string_buffer(5000 + 8)

            def native(f, *head):
                def call(data, offset):
                    C.memmove(C.addressof(buffer) + offset, data, len(data))
                    return f(*head, C.addressof(buffer) + offset, len(data))

                return call

            small_crc.zip_crc.restype = C.c_uint32
            small_crc.zip_crc.argtypes = [C.c_void_p, C.c_uint32]
            functions.append(native(small_crc.zip_crc))
            for name in ('crc_update', 'crc_update_volatile'):
                f = getattr(small_crc, name)
                f.restype = C.c_uint32
                f.argtypes = [C.c_uint32, C.c_void_p, C.c_uint32]
                functions.append(native(f, 0))
                for sample in (b'', b'123456789', bytes(range(256)) * 15):
                    split = len(sample) // 3
                    first = C.create_string_buffer(sample[:split])
                    last = C.create_string_buffer(sample[split:])
                    self.assertEqual(f(f(0, first, split), last, len(sample) - split), zlib.crc32(sample))
            rng = random.Random(77)
            data = bytes(rng.getrandbits(8) for _ in range(5000))
            for offset in range(4):
                for length in (0, 1, 2, 3, 4, 5, 7, 8, 9, 63, 64, 65, 4999, 5000):
                    for f in functions:
                        self.assertEqual(f(data[:length], offset), zlib.crc32(data[:length]), (offset, length))
