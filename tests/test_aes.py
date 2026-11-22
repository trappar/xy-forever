import random
import subprocess
import unittest

import pytest
import xyf_hosttest


@pytest.mark.gate
class NativeAesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.native = xyf_hosttest.module('aes')

    def decrypt(self, key, data):
        return self.native.decrypt(key, data)

    def test_fips197_aes256_known_answer(self):
        self.assertEqual(
            self.decrypt(bytes(range(32)), bytes.fromhex('8ea2b7ca516745bfeafc49904b496089')),  # FIPS-197 C.3 vector
            bytes.fromhex('00112233445566778899aabbccddeeff'),  # FIPS-197 C.3 vector
        )

    def test_random_public_vectors_match_independent_openssl(self):
        rng = random.Random(197)
        for _ in range(30):
            key = rng.randbytes(32)
            clear = rng.randbytes(16)
            encrypted = subprocess.run(
                ['openssl', 'enc', '-aes-256-ecb', '-nopad', '-K', key.hex()],
                input=clear,
                capture_output=True,
                check=True,
            ).stdout
            self.assertEqual(self.decrypt(key, encrypted), clear)
