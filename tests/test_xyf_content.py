"""The content switch: the path rule, the record's head and the job over an in-memory file system,
with a fault, a power cut and A going silent at every request (target/xyf_content_test.cpp)."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from xy_forever import hostbuild  # noqa: E402


@pytest.mark.gate
class ContentSwitchTest(unittest.TestCase):
    def test_rule_head_round_trip_and_faults(self):
        with tempfile.TemporaryDirectory(prefix='xyf-content-') as tmp:
            binary = Path(tmp) / 'content'
            hostbuild.run(['/usr/bin/clang++', '-std=c++14', '-Wall', '-Wextra', '-Werror',
                           '-fsanitize=address,undefined', '-fno-omit-frame-pointer', '-I', str(ROOT / 'target'),
                           str(ROOT / 'target/xyf_store.cpp'), str(ROOT / 'target/xyf_info.cpp'), str(ROOT / 'native/hash.cpp'),
                           str(ROOT / 'native/json.cpp'), str(ROOT / 'target/xyf_manifest.cpp'),
                           str(ROOT / 'target/xyf_package_record.cpp'),
                           str(ROOT / 'target/xyf_content_test.cpp'), '-o', str(binary)],
                          check=True, capture_output=True, text=True)
            result = subprocess.run([str(binary)], check=True, capture_output=True, text=True)
            self.assertRegex(result.stdout, r'[1-9][0-9]* checks passed')


if __name__ == '__main__':
    unittest.main()
