"""Native verified blob publication and streaming GC, including every cut boundary."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from xy_forever import hostbuild

ROOT = Path(__file__).resolve().parents[1]


class BlobTest(unittest.TestCase):
    def test_copy_gc_shared_references_and_every_cut(self):
        with tempfile.TemporaryDirectory(prefix='xyf-blob-') as tmp:
            binary = Path(tmp) / 'blob'
            hostbuild.run(['/usr/bin/clang++', '-std=c++11', '-Wall', '-Wextra', '-Werror',
                           '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                           str(ROOT / 'target/xyf_blob_test.cpp'), str(ROOT / 'target/xyf_filelist.cpp'),
                           str(ROOT / 'native/hash.cpp'), '-o', str(binary)], check=True, capture_output=True)
            subprocess.run([str(binary)], check=True, capture_output=True)
