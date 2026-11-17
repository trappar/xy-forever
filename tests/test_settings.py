"""The settings record (/yaffs2/xy-forever/settings.txt) and the confirm gate, target/xyf_settings.hpp, on the
host: defaults when absent (confirm on), exact values, round trip, a record with the old knob= key, the page row
(Mod memory) and its texts, and the gate (only
a real PLAY / STOP answers; injected keys and releases are ignored; 30 s bound, wrap-safe)."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from xy_forever import hostbuild  # noqa: E402


class SettingsTests(unittest.TestCase):
    def test_record_page_and_gate(self):
        with tempfile.TemporaryDirectory(prefix='xyf-settings-') as temp:
            binary = Path(temp) / 'settings'
            hostbuild.run(['/usr/bin/clang++', '-std=c++11', '-Wall', '-Wextra', '-Werror',
                           '-fsanitize=address,undefined', str(ROOT / 'target/xyf_settings_test.cpp'),
                           '-o', str(binary)], check=True, capture_output=True, text=True)
            result = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertRegex(result.stdout, r'[1-9][0-9]* checks passed')


if __name__ == '__main__':
    unittest.main()
