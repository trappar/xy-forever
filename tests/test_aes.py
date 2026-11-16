import ctypes
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from xy_forever import hostbuild  # noqa: E402

class NativeAesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory(prefix='xyf-aes-')
        path=Path(cls.tmp.name)/'aes.dylib'
        hostbuild.run(['/usr/bin/clang++','-std=c++11','-Wall','-Wextra','-Werror','-shared','-fPIC',
                       str(ROOT/'native/aes.cpp'),str(ROOT/'tests/aes_bridge.cpp'),'-o',str(path)],check=True)
        cls.lib=ctypes.CDLL(str(path))
        cls.lib.aes_decrypt.argtypes=[ctypes.c_void_p]*3

    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()

    def decrypt(self,key,data):
        output=ctypes.create_string_buffer(16)
        self.assertEqual(self.lib.aes_decrypt(key,data,output),0)
        return output.raw

    def test_fips197_aes256_known_answer(self):
        self.assertEqual(self.decrypt(bytes(range(32)),bytes.fromhex('8ea2b7ca516745bfeafc49904b496089')),  # FIPS-197 C.3 vector
                         bytes.fromhex('00112233445566778899aabbccddeeff'))  # FIPS-197 C.3 vector

    def test_random_public_vectors_match_independent_openssl(self):
        rng=random.Random(197)
        for _ in range(30):
            key=rng.randbytes(32);clear=rng.randbytes(16)
            encrypted=subprocess.run(['openssl','enc','-aes-256-ecb','-nopad','-K',key.hex()],
                                     input=clear,capture_output=True,check=True).stdout
            self.assertEqual(self.decrypt(key,encrypted),clear)
