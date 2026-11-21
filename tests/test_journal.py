"""Durable intent: every publication IO boundary, canonical/newer formats and source identity."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from xy_forever import hostbuild
ROOT = Path(__file__).resolve().parents[1]


class Journal(unittest.TestCase):
    def test_publication_and_source_identity(self):
        with tempfile.TemporaryDirectory(prefix='xyf-journal-') as tmp:
            binary = Path(tmp) / 'journal'
            hostbuild.run(['/usr/bin/clang++', '-std=c++14', '-Wall', '-Wextra', '-Werror',
                           '-fsanitize=address,undefined', '-fno-omit-frame-pointer', '-I', str(ROOT / 'target'),
                           str(ROOT / 'tests/journal_test.cpp'), str(ROOT / 'target/xyf_store.cpp'),
                           str(ROOT / 'target/xyf_info.cpp'), str(ROOT / 'target/xyf_crc32.cpp'),
                           str(ROOT / 'native/json.cpp'), str(ROOT / 'target/xyf_manifest.cpp'),
                           str(ROOT / 'target/xyf_package_record.cpp'),
                           str(ROOT / 'native/hash.cpp'), '-o', str(binary)], check=True, capture_output=True, text=True)
            subprocess.run([str(binary)], check=True, capture_output=True, text=True)
