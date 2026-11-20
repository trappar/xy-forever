"""The settings record (/yaffs2/xy-forever/settings.txt) and the confirm gate, target/xyf_settings.hpp, on the
host: defaults when absent (confirm on, PLAY held, MTP + Serial), exact values (usb=mtp from before MTP alone: MTP + Serial), round trip, a record with the old
knob= key, one before power_on / usb (#21), a newer format (the defaults, newer set), the page rows and their texts,
and the gate (only
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

    def test_usb_mode_and_power_on_reach_the_product(self):
        """#21: the product's one USB start of a menu waits (bounded) for settings.txt, then starts the composite only
        when USB mode is not Serial (else the stock serial service, its own fallback), without its CDC interfaces for
        MTP. A reads Menu at power-on
        with the default's check (settings.txt, else settings.old) before it builds the check's status in the same
        scratch, and B opens the menu only for always after a good check. (The simulator has no composite:
        tests/test_sim_settings.py checks the rest.)"""
        composite = (ROOT / 'target/usb_service_composite.ipp').read_text()
        self.assertIn('const bool trial = settings().usb != xyf::settings::usb_serial;', composite)
        self.assertIn('const bool serial = settings().usb != xyf::settings::usb_mtp_only;', composite)
        self.assertIn('mu::begin(mu::stock_port(), serial)', composite)
        home = (ROOT / 'target/usb_service_home.ipp').read_text()
        wait = home.index('if (usb_deferred && !settings_load() && now() - usb_deferred_ms < usb_settings_ms) return 0;')
        self.assertLess(wait, home.index('if (usb_deferred) { usb_deferred = 0; usb_begin(); return 0; }'))
        a_menu = (ROOT / 'target/file_boot_a_menu.ipp').read_text()
        self.assertLess(a_menu.index('a_power_on(storage, mounted)'), a_menu.index('xyf_boot::CheckStatus& s = '))
        b_menu = (ROOT / 'target/file_boot_b_menu.ipp').read_text()
        self.assertIn('if (reason == reason_ok && b_status().power_on == xyf::settings::power_always)', b_menu)


if __name__ == '__main__':
    unittest.main()
