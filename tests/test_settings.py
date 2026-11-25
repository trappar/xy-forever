"""The settings record (/yaffs2/xy-forever/settings.txt) and the confirm gate, target/xyf_settings.hpp, on the
host (tests/native/xyf_settings_test.cpp): defaults when absent (confirm on, PLAY held, MTP + Serial), exact values (usb=mtp from before MTP alone: MTP + Serial), round trip, a record with the old
knob= key, one before power_on / usb (#21), a newer format (the defaults, newer set), the page rows and their texts,
and the gate (only
a real PLAY / STOP answers; injected keys and releases are ignored; 30 s bound, wrap-safe)."""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class SettingsTests(unittest.TestCase):
    def test_usb_mode_and_power_on_reach_the_product(self):
        """#191: the product's one USB start of a menu waits (bounded) for settings.txt, then always starts the composite
        (MTP + serial), whatever USB mode says: never a driver change within a boot. The mode is enforced live in our
        own code: MTP refuses every serial request but hello (serial_off; hello serial=off, the MTP tunnel aside),
        Serial makes the MTP responder refuse every operation but GetDeviceInfo. A reads Menu at power-on
        with the default's check (settings.txt, else settings.old) before it builds the check's status in the same
        scratch, and B opens the menu only for always after a good check. (The simulator has no composite:
        the simulator checked the rest until #233.)"""
        composite = (ROOT / 'target/usb_service_composite.ipp').read_text()
        self.assertNotIn('settings()', composite)
        self.assertIn('    const bool trial = true;\n#endif', composite)
        self.assertIn('mu::begin(mu::stock_port())', composite)
        self.assertNotIn('kConfigMtp', (ROOT / 'target/mtp_usb.cpp').read_text())  # #186's MTP alone: gone
        settings = (ROOT / 'target/usb_service_settings.ipp').read_text()
        self.assertIn('bool serial_off() { return settings_record.usb == xs::usb_mtp_only; }', settings)
        self.assertIn('bool mtp_off() { return settings_record.usb == xs::usb_serial; }', settings)
        session = (ROOT / 'target/usb_service_mtp_session.ipp').read_text()
        self.assertIn('m.responder.refuse(xy_forever::usb_service::mtp_off() ? mr::r_access_denied : 0);', session)
        home_text = (ROOT / 'target/usb_service_home.ipp').read_text()
        self.assertIn('off = type != P::t_hello && serial_refuses(type);', home_text)
        self.assertIn('else if (off) status_only(type, seq, P::s_serial_off);', home_text)
        home = (ROOT / 'target/usb_service_home.ipp').read_text()
        wait = home.index(
            'if (usb_deferred && !settings_load() && now() - usb_deferred_ms < usb_settings_ms) return 0;'
        )
        self.assertLess(
            wait,
            home.index(
                'if (usb_deferred) {\n        usb_deferred = 0;\n        usb_begin();\n        return 0;\n    }'
            ),
        )
        a_menu = (ROOT / 'target/file_boot_a_menu.ipp').read_text()
        self.assertLess(a_menu.index('a_power_on(storage, mounted)'), a_menu.index('xyf_boot::CheckStatus& s = '))
        b_menu = (ROOT / 'target/file_boot_b_menu.ipp').read_text()
        self.assertIn(
            'if ((reason == reason_ok || reason == reason_content) && b_status().power_on == xyf::settings::power_always)',
            b_menu,
        )


if __name__ == '__main__':
    unittest.main()
