"""The Settings page's rows in the simulator (#21): Menu at power-on (PLAY held, always), USB mode (MTP + Serial,
MTP, Serial), About (the About page, back to Settings; the home menu has no Info row). A setting's PLAY opens its
option list (- / + move, PLAY picks, STOP leaves it unchanged); a pick changes only the page (the row marked), and
leaving the page with a change writes /yaffs2/xy-forever/settings.txt once through the config job (one read-write
window, no catalog fetch); the home menu's line says when it is saved; a power-on reads it back. Menu at power-on decides a plain power-on (A reads the record with the default's check): always opens the
menu without PLAY, PLAY held boots at once. A newer record (format=2) reads as the defaults and no row changes it;
a broken record reads as the defaults and a plain power-on boots. The simulator has no composite USB device (sim:
stock serial only), so USB mode is checked as a stored row here and in the product source (tests/test_settings.py;
the driver's MTP alone: target/mtp_usb_test.cpp). On the OP-XY, MTP leaves no serial: the host would not reattach.
Driven by injected keys. Needs clang++ and the private stock 1.2.1 images (skipped without them). No device
access."""
import contextlib
import io
import json
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
import xyf_host as host                                                    # noqa: E402
import test_sim_mod_job as J                                               # noqa: E402
from test_sim_mods_page import MINUS, PLAY, PLUS, STOP, PageBase, synthetic_setup   # noqa: E402

MAIN, INFO, SETTINGS, OPTIONS = 0, 2, 6, 7                                 # xyf_menu Screen
SAVED, SAVED_USB = 'menu: line: Settings saved.', 'menu: line: Settings saved: USB mode changes'
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
        """PLAY on Settings row `row` (from row 0), back to row 0; until the line `note` (the PLAY's note)."""
        seen = self.count(note)
        for _ in range(row):
            self.press(PLUS)
        self.press(PLAY)
        self.until(lambda: self.count(note) > seen, note)
        for _ in range(row):
            self.press(MINUS)

    def pick(self, row, moves):
        """Settings row `row` (from row 0): PLAY opens its options, `moves` x + (- when negative), PLAY picks; back
        to row 0. The option picked."""
        opened, picks = self.count('settings: options current'), self.count('settings: pick on row %d:' % row)
        for _ in range(row):
            self.press(PLUS)
        self.press(PLAY)
        self.until(lambda: self.count('settings: options current') > opened, 'the option list')
        for _ in range(abs(moves)):
            self.press(PLUS if moves > 0 else MINUS)
        self.press(PLAY)
        self.until(lambda: self.count('settings: pick on row %d:' % row) > picks, 'the pick')
        for _ in range(row):
            self.press(MINUS)
        return int(self.lines('settings: pick on row %d: option ' % row)[-1])

    def leave(self, note):
        """STOP: the page left; until the home menu's line `note` (the save's end)."""
        seen = self.count(note)
        self.press(STOP)
        self.until(lambda: self.count(note) > seen, note)

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
    def packs_command(self):
        with patch.object(host, 'connect', return_value=(self.dev, 'sim', self.dev.hello_info.text)), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(host.main(['packs', 'list']), 0)
        return json.loads(out.getvalue().split('\n', 1)[1])

    def test_installed_pack_ids_and_vendor_metadata(self):
        self.assertEqual(self.packs_command(), [])
        root = self.root / 'content/dlc'
        root.mkdir(exist_ok=True)
        ids = set()
        for n in range(9):
            pack = root / ('synth-%d' % n)
            pack.mkdir()
            (pack / 'manifest.json').write_text(json.dumps({'name': 'Sounds %d' % n, 'version': '1.0'}))
            (pack / 'sound.wav').write_bytes(b'RIFF' + bytes([n]))
            ids.add(pack.name)
        (root / 'synth-0/manifest.json').write_text('bad')
        (root / 'stray').mkdir()
        (root / 'linked').symlink_to(root / 'synth-1', target_is_directory=True)
        (root / 'stray/manifest.json').symlink_to(root / 'synth-1/manifest.json')
        rows = {r['id']: r for r in self.packs_command()}
        self.assertEqual(rows.keys(), ids)
        self.assertEqual(rows['synth-8'], dict(id='synth-8', name='Sounds 8', version='1.0'))
        self.assertIn('invalid manifest', rows['synth-0']['error'])

        self.settings_page()
        self.pick(1, 1)                                                   # changes Menu at power-on
        saved = self.record()
        self.press(MINUS)                                                 # last Settings row is Content packs
        self.press(PLAY)
        self.until(lambda: self.count('pack page 0: 7 entries, more=1'), 'first seven packs')
        first = self.lines('pack id: ')[-7:]
        self.press(PLUS)
        self.until(lambda: self.count('pack page 1: 2 entries, more=0'), 'last two packs')
        self.assertEqual(set(first + self.lines('pack id: ')[-2:]), ids)
        self.press(MINUS)
        self.until(lambda: self.count('pack page 0: 7 entries, more=1') >= 2, 'back to page one')
        self.assertEqual(self.lines('pack id: ')[-7:], first)
        self.press(STOP)
        self.assertEqual(self.record(), saved)                            # still inside Settings
        self.leave(SAVED)
        self.assertEqual(self.record()['power_on'], 'always')

    def test_empty_pack_page(self):
        self.settings_page()
        self.press(MINUS)
        self.press(PLAY)
        self.until(lambda: self.count('pack page 0: 0 entries, more=0'), 'empty installed list')
        self.press(STOP)
        self.until(lambda: self.count('menu: screen %d row 4 ' % SETTINGS) >= 2, 'back to Settings')

    def test_home_has_no_info_row(self):
        self.until(lambda: self.lines('settings: confirm=0'), 'the settings read')
        self.press(MINUS)                                                  # wraps to the last row: Settings
        self.press(PLAY)
        self.until(lambda: self.lines('menu: screen %d row 4 ' % SETTINGS), 'Settings, the last of five rows')

    def test_menu_at_power_on_always_then_play_held(self):
        self.until(lambda: self.lines('settings: confirm=0 power_on=0 usb=0 newer=0'), 'the settings read')
        self.settings_page()
        self.assertEqual(self.pick(1, 1), 1)                               # PLAY held -> Always
        self.leave(SAVED)
        self.assertEqual(self.record()['power_on'], 'always')
        self.assertEqual(self.record()['format'], '1')
        self.power(play=False)                                             # no PLAY: the menu opens anyway
        self.until(lambda: self.after_power_on('menu (home)'), 'the menu without PLAY')
        self.reconnect()                                                   # with USB, as with PLAY
        self.assertFalse(self.in_os())
        self.until(lambda: self.after_power_on('settings: confirm=0 power_on=1'), 'the record read back')
        self.settings_page()
        self.assertEqual(self.pick(1, -1), 0)                              # Always -> PLAY held
        self.leave(SAVED)
        self.assertEqual(self.record()['power_on'], 'play')
        self.power(play=False)                                             # PLAY held: a plain power-on boots
        self.until(self.in_os, 'the OS')
        self.assertFalse(self.after_power_on('menu (home)'))

    def test_usb_mode_and_about(self):
        self.until(lambda: self.lines('settings: confirm=0 power_on=0 usb=0'), 'the settings read')
        self.settings_page()
        self.assertEqual(self.pick(2, -1), 2)                              # MTP + Serial -> Serial (- wraps)
        self.leave(SAVED_USB)
        self.assertEqual(self.record()['usb'], 'serial')
        self.power(play=True)
        self.reconnect()                                                   # stock serial: the host still attaches
        self.until(lambda: self.after_power_on('settings: confirm=0 power_on=0 usb=2'), 'USB mode read back')
        self.settings_page()
        self.assertEqual(self.pick(2, 1), 0)                               # back to MTP + Serial (wraps)
        self.leave(SAVED_USB)
        self.assertEqual(self.record()['usb'], 'mtp_serial')
        self.press(PLAY)                                                   # Settings again (still the home row)
        for _ in range(2):
            self.press(PLUS)
        seen = self.count('menu: info image: ')
        self.press(PLUS)                                                   # About
        self.press(PLAY)
        self.until(lambda: self.count('menu: info image: ') > seen, 'the About page')
        screens = self.count('menu: screen %d ' % SETTINGS)
        self.press(STOP)                                                   # back to Settings
        self.until(lambda: self.count('menu: screen %d ' % SETTINGS) > screens, 'back to Settings')


    def test_usb_mode_mtp_alone(self):
        """MTP (MTP alone): stored as usb=mtp_only, read back after a power-on, and back to MTP + Serial. (The
        simulator keeps its stock serial: on the OP-XY only the device's own Settings page changes it back.)"""
        self.until(lambda: self.lines('settings: confirm=0 power_on=0 usb=0'), 'the settings read')
        self.settings_page()
        self.assertEqual(self.pick(2, 1), 1)                               # MTP + Serial -> MTP
        self.leave(SAVED_USB)
        self.assertEqual(self.record()['usb'], 'mtp_only')
        self.power(play=True)
        self.reconnect()
        self.until(lambda: self.after_power_on('settings: confirm=0 power_on=0 usb=1'), 'USB mode read back')
        self.settings_page()
        self.assertEqual(self.pick(2, -1), 0)                              # MTP -> MTP + Serial
        self.leave(SAVED_USB)
        self.assertEqual(self.record()['usb'], 'mtp_serial')

    def test_option_list_and_one_save_on_leaving(self):
        """The list opens on the value in effect; STOP leaves it unchanged; picks write nothing (the rows marked),
        leaving writes both at once (one read-write window, no catalog fetch); leaving without a change writes
        nothing."""
        self.until(lambda: self.lines('settings: confirm=0 power_on=0 usb=0 newer=0'), 'the settings read')
        before = (self.root / 'xy-forever/settings.txt').read_bytes()
        self.settings_page()
        opened = self.count('settings: options current')
        self.press(PLUS)                                                   # Menu at power-on
        self.press(PLAY)
        self.until(lambda: self.count('settings: options current') > opened, 'the option list')
        self.assertEqual(self.lines('settings: options current ')[-1], '0')   # PLAY held, in effect
        self.press(PLUS)                                                   # Always selected...
        screens = self.count('menu: screen %d ' % SETTINGS)
        self.press(STOP)                                                   # ...but STOP: nothing picked
        self.until(lambda: self.count('menu: screen %d ' % SETTINGS) > screens, 'back on the page')
        self.assertFalse(self.lines('settings: pick on row'))
        self.press(MINUS)
        windows = self.sim.status()['rw_mounts']
        self.assertEqual(self.pick(1, 1), 1)                               # Always
        self.assertEqual(self.pick(2, -1), 2)                              # Serial (- wraps)
        self.until(lambda: self.lines('settings: confirm=0 power_on=1 usb=2 newer=0'), 'both shown at once')
        self.assertEqual((self.root / 'xy-forever/settings.txt').read_bytes(), before)   # nothing written yet
        self.assertEqual(self.sim.status()['rw_mounts'], windows)
        self.leave(SAVED_USB)                                              # one write, both values
        self.assertEqual((self.record()['power_on'], self.record()['usb'], self.record()['format']), ('always', 'serial', '1'))
        self.assertEqual(self.sim.status()['rw_mounts'], windows + 1)
        self.press(PLAY)                                                   # Settings again (no catalog fetch reset the
                                                                           # home row to Continue), and out: no change
        self.until(lambda: self.count('menu: screen %d ' % SETTINGS) > screens + 1, 'the page again')
        self.press(STOP)
        self.until(lambda: self.count('menu: screen %d ' % MAIN) and self.lines('menu: screen ')[-1].startswith('%d ' % MAIN),
                   'the home menu')
        self.sim.advance(2000)
        self.assertEqual(self.sim.status()['rw_mounts'], windows + 1)       # nothing written


@unittest.skipIf(J.UNAVAILABLE, J.UNAVAILABLE)
class EarlierRecord(Rows, unittest.TestCase):
    settings_text = b'format=1\nconfirm=0\npower_on=play\nusb=mtp\n'        # written before MTP alone existed

    def test_usb_mtp_reads_as_mtp_and_serial(self):
        self.until(lambda: self.lines('settings: confirm=0 power_on=0 usb=0 newer=0'), 'the record read: MTP + Serial')


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
