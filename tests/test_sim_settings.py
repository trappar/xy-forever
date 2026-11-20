"""The Settings page's rows in the simulator (#21): Menu at power-on (PLAY held, always), USB mode (Serial + MTP,
Serial only), About (the About page, back to Settings; the home menu has no Info row). Each setting's PLAY writes
/yaffs2/xy-forever/settings.txt through the config job and the page says when it is saved; a power-on reads it
back. Menu at power-on decides a plain power-on (A reads the record with the default's check): always opens the
menu without PLAY, PLAY held boots at once. A newer record (format=2) reads as the defaults and no row changes it;
a broken record reads as the defaults and a plain power-on boots. The simulator has no composite USB device (sim:
stock serial only), so USB mode is checked as a stored row here and in the product source (tests/test_settings.py).
Driven by injected keys. Needs clang++ and the private stock 1.2.1 images (skipped without them). No device
access."""
from pathlib import Path
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
import xyf_host as host                                                    # noqa: E402
import test_sim_mod_job as J                                               # noqa: E402
from test_sim_mods_page import MINUS, PLAY, PLUS, STOP, PageBase, synthetic_setup   # noqa: E402

MAIN, INFO, SETTINGS = 0, 2, 6                                             # xyf_menu Screen
SAVED, SAVED_USB = 'note=Saved.', 'note=Saved: USB mode changes'
NEWER = 'note=settings.txt is from a newer XY Forever'


class Rows(PageBase):
    base = 'modbase'
    unverified = True

    @classmethod
    def setUpClass(cls):
        synthetic_setup(cls)

    # -- helpers ---------------------------------------------------------------------------------
    def until(self, cond, what, timeout=30.0):
        deadline = time.monotonic() + timeout
        while True:
            got = cond()
            if got:
                return got
            self.assertLess(time.monotonic(), deadline, 'never ' + what)
            time.sleep(0.01)

    def count(self, marker):
        return len(self.lines(marker))

    def record(self):
        path = self.root / 'xy-forever/settings.txt'
        return dict(line.split('=', 1) for line in path.read_text().splitlines() if '=' in line)

    def settings_page(self):
        """Home (Continue) -> Settings, the last row."""
        seen = self.count('menu: screen %d ' % SETTINGS)
        self.press(MINUS)
        self.press(PLAY)
        self.until(lambda: self.count('menu: screen %d ' % SETTINGS) > seen, 'the Settings page')

    def play_row(self, row, note):
        """PLAY on Settings row `row` (from row 0), back to row 0; until the page's note says `note`."""
        seen = self.count(note)
        for _ in range(row):
            self.press(PLUS)
        self.press(PLAY)
        self.until(lambda: self.count(note) > seen, note)
        for _ in range(row):
            self.press(MINUS)

    def power(self, play):
        """A power cycle (PLAY held or not); the 'power on' lines before it."""
        ons = self.count('power on (control')
        self.assertTrue(self.sim.control('power play' if play else 'power plain').get('ok'))
        self.until(lambda: self.count('power on (control') > ons, 'the power-on')
        return ons

    def after_power_on(self, marker):
        """The lines with marker since the last control power-on."""
        log = (self.dir / 'log.txt').read_text(errors='replace')
        tail = log[log.rindex('power on (control'):]
        return [line for line in tail.splitlines() if marker in line]

    def in_os(self):
        return self.sim.status()['a'] == 'os' and self.sim.status()['b'] == 'os'

    def reconnect(self):
        self.dev.t.close()
        self.sim.wait_listening(30.0)
        self.dev = host.Device(host.TcpPort(self.sim.address), on_log=None)
        self.assertEqual(self.dev.hello()[0], 0)


@unittest.skipIf(J.UNAVAILABLE, J.UNAVAILABLE)
class SettingsRows(Rows, unittest.TestCase):
    def test_home_has_no_info_row(self):
        self.until(lambda: self.lines('settings: confirm=0'), 'the settings read')
        self.press(MINUS)                                                  # wraps to the last row: Settings
        self.press(PLAY)
        self.until(lambda: self.lines('menu: screen %d row 4 ' % SETTINGS), 'Settings, the last of five rows')

    def test_menu_at_power_on_always_then_play_held(self):
        self.until(lambda: self.lines('settings: confirm=0 power_on=0 usb=0 newer=0'), 'the settings read')
        self.settings_page()
        self.play_row(1, SAVED)                                            # PLAY held -> Always
        self.assertEqual(self.record()['power_on'], 'always')
        self.assertEqual(self.record()['format'], '1')
        self.power(play=False)                                             # no PLAY: the menu opens anyway
        self.until(lambda: self.after_power_on('menu (home)'), 'the menu without PLAY')
        self.reconnect()                                                   # with USB, as with PLAY
        self.assertFalse(self.in_os())
        self.until(lambda: self.after_power_on('settings: confirm=0 power_on=1'), 'the record read back')
        self.settings_page()
        self.play_row(1, SAVED)                                            # Always -> PLAY held
        self.assertEqual(self.record()['power_on'], 'play')
        self.power(play=False)                                             # PLAY held: a plain power-on boots
        self.until(self.in_os, 'the OS')
        self.assertFalse(self.after_power_on('menu (home)'))

    def test_usb_mode_and_about(self):
        self.until(lambda: self.lines('settings: confirm=0 power_on=0 usb=0'), 'the settings read')
        self.settings_page()
        self.play_row(2, SAVED_USB)                                        # Serial + MTP -> Serial only
        self.assertEqual(self.record()['usb'], 'serial')
        self.power(play=True)
        self.reconnect()                                                   # stock serial: the host still attaches
        self.until(lambda: self.after_power_on('settings: confirm=0 power_on=0 usb=1'), 'USB mode read back')
        self.settings_page()
        self.play_row(2, SAVED_USB)                                        # back to Serial + MTP
        self.assertEqual(self.record()['usb'], 'mtp')
        for _ in range(2):
            self.press(PLUS)
        seen = self.count('menu: info image: ')
        self.press(PLUS)                                                   # About
        self.press(PLAY)
        self.until(lambda: self.count('menu: info image: ') > seen, 'the About page')
        screens = self.count('menu: screen %d ' % SETTINGS)
        self.press(STOP)                                                   # back to Settings
        self.until(lambda: self.count('menu: screen %d ' % SETTINGS) > screens, 'back to Settings')


@unittest.skipIf(J.UNAVAILABLE, J.UNAVAILABLE)
class NewerRecord(Rows, unittest.TestCase):
    settings_text = b'format=2\nconfirm=0\npower_on=always\nusb=serial\nlater=1\n'

    def test_newer_reads_as_defaults_and_is_kept(self):
        self.until(lambda: self.lines('settings: confirm=1 power_on=0 usb=0 newer=1'), 'the newer record read')
        self.settings_page()
        self.play_row(1, NEWER)                                            # Menu at power-on: refused
        self.play_row(2, NEWER)                                            # USB mode: refused
        self.assertEqual(self.record()['format'], '2')                     # unchanged
        self.power(play=False)                                             # A refuses it too: PLAY held, a boot
        self.until(self.in_os, 'the OS')


@unittest.skipIf(J.UNAVAILABLE, J.UNAVAILABLE)
class BrokenRecord(Rows, unittest.TestCase):
    settings_text = b'power_on=always\n' + b'\xff' * 400                  # longer than a record can be

    def test_broken_record_boots_plain(self):
        self.until(lambda: self.lines('settings: confirm='), 'the settings read')
        self.power(play=False)                                             # never a hang: the default boots
        self.until(self.in_os, 'the OS')
        self.assertFalse(self.after_power_on('menu (home)'))


if __name__ == '__main__':
    unittest.main()
