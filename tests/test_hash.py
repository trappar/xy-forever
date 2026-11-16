import ctypes
import hashlib
from pathlib import Path
import random
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from xy_forever import hostbuild  # noqa: E402

class NativeHashTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='xyf-hash-')
        path = Path(cls.tmp.name) / 'hash.dylib'
        hostbuild.run(['/usr/bin/clang++', '-std=c++11', '-Wall', '-Wextra', '-Werror',
                       '-shared', '-fPIC', str(ROOT / 'native/hash.cpp'),
                       str(ROOT / 'tests/hash_bridge.cpp'), '-o', str(path)], check=True)
        cls.lib = ctypes.CDLL(str(path))
        cls.lib.sha256_digest.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def check(self, data, chunk):
        output = ctypes.create_string_buffer(32)
        self.assertEqual(self.lib.sha256_digest(data, len(data), chunk, output), 0)
        self.assertEqual(output.raw, hashlib.sha256(data).digest())

    def test_standard_and_padding_boundaries(self):
        for data in (b'', b'abc', b'a' * 1000000,
                     *[bytes(range(n)) for n in range(50, 75)]):
            for chunk in (1, 63, 64, 127, 65536):
                with self.subTest(size=len(data), chunk=chunk):
                    self.check(data, chunk)

    def test_aligned_and_misaligned_inputs(self):
        rng = random.Random(9150)
        data = rng.randbytes(5000)
        for offset in range(4):                    # whole blocks are read in place, by word when aligned
            buffer = ctypes.create_string_buffer(len(data) + 8)
            ctypes.memmove(ctypes.addressof(buffer) + offset, data, len(data))
            for chunk in (1, 64, 100, 4096, 5000):
                with self.subTest(offset=offset, chunk=chunk):
                    output = ctypes.create_string_buffer(32)
                    self.assertEqual(self.lib.sha256_digest(ctypes.addressof(buffer) + offset, len(data), chunk,
                                                            output), 0)
                    self.assertEqual(output.raw, hashlib.sha256(data).digest())

    def test_random_streams_with_arbitrary_chunk_boundaries(self):
        rng = random.Random(6234)
        for _ in range(60):
            data = rng.randbytes(rng.randrange(0, 10000))
            self.check(data, rng.randrange(1, 600))
