"""Read-only synthetic memory model; no device, MMIO or CPLB emulation."""
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
class RetainedLookupTests(unittest.TestCase):
    def test_corrupt_registry_has_bounded_reads_and_never_returns_partial_match(self):
        with tempfile.TemporaryDirectory(prefix='xyf-retained-lookup-') as directory:
            binary = Path(directory) / 'lookup'
            hostbuild.run(['/usr/bin/clang++', '-std=c++11', '-Wall', '-Wextra', '-Werror',
                           '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                           str(ROOT / 'target/retained_lookup.cpp'),
                           str(ROOT / 'target/retained_lookup_test.cpp'), '-o', str(binary)],
                          check=True, capture_output=True, text=True)
            result = subprocess.run([str(binary)], check=True, capture_output=True,
                                    text=True, timeout=20)
            self.assertIn('22 retained lookup checks passed', result.stdout)


if __name__ == '__main__':
    unittest.main()
