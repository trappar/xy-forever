import hashlib
import random
import unittest

import xyf_hosttest


class NativeHashTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.native = xyf_hosttest.module('hash')

    def check(self, data, chunk):
        self.assertEqual(self.native.sha256(data, chunk), hashlib.sha256(data).digest())

    def test_standard_and_padding_boundaries(self):
        for data in (b'', b'abc', b'a' * 1000000, *[bytes(range(n)) for n in range(50, 75)]):
            for chunk in (1, 63, 64, 127, 65536):
                with self.subTest(size=len(data), chunk=chunk):
                    self.check(data, chunk)

    def test_aligned_and_misaligned_inputs(self):
        rng = random.Random(9150)
        data = rng.randbytes(5000)
        for offset in range(4):  # whole blocks are read in place, by word when aligned
            for chunk in (1, 64, 100, 4096, 5000):
                with self.subTest(offset=offset, chunk=chunk):
                    self.assertEqual(self.native.sha256(data, chunk, offset), hashlib.sha256(data).digest())

    def test_random_streams_with_arbitrary_chunk_boundaries(self):
        rng = random.Random(6234)
        for _ in range(60):
            data = rng.randbytes(rng.randrange(0, 10000))
            self.check(data, rng.randrange(1, 600))
