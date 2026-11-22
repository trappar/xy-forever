"""The Settings page's rows in the simulator (#21): Menu at power-on (PLAY held, always), USB mode (MTP + Serial,
MTP, Serial), About (the About page, back to Settings; the home menu has no Info row). A setting's PLAY opens its
option list (- / + move, PLAY picks, STOP leaves it unchanged). #191: a pick takes effect at once and saves on its
own, without leaving the page: /yaffs2/xy-forever/settings.txt written once through the config job at a quiet moment
(quick picks: one write, one read-write window, no catalog fetch); a power-on reads it back. Menu at power-on decides
a plain power-on (A reads the record with the default's check): always opens the menu without PLAY, PLAY held boots
at once. USB mode switches live within one boot, never re-enumerating (the same connection throughout): MTP refuses
every serial request but hello (serial_off; hello serial=off; the MTP tunnel stays), Serial refuses MTP sessions
(Access_Denied over the tunnel). A newer record (format=2) reads as the defaults and no row changes it; a broken
record reads as the defaults and a plain power-on boots. The simulator has no composite (its USB is the stock
serial and the MTP tunnel); the product start is checked in tests/test_settings.py. Driven by injected keys (real
keys while serial is off). Needs clang++ and the private stock 1.2.1 images (skipped without them). No device
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
from test_sim_config import MARK, mark_text                               # noqa: E402
from test_sim_mtp_session import TunnelClient                             # noqa: E402
from xy_forever import host_trust, usb_proto as P, usb_proto_ids as ids     # noqa: E402

MAIN, INFO, SETTINGS, OPTIONS = 0, 1, 3, 4                                 # xyf_menu Screen (#197)
R_OK = 0x2001                                                              # MTP OK
SAVED = 'menu: line: Settings saved.'
OPEN_SESSION, GET_DEVICE_INFO, GET_STORAGE_IDS, ACCESS_DENIED = 0x1002, 0x1001, 0x1004, 0x200F
NEWER = 'note=settings.txt is from a newer XY Forever'


class Rows(PageBase):
    base = 'modbase'
    unverified = True

    @classmethod
    def setUpClass(cls):
        synthetic_setup(cls)
        (cls.seeded / MARK).write_text(mark_text(cls.seeded, cls.base))   # converged once (#197): plain boots at once

    real = False                                                           # keys as the OP-XY's own (serial off)

    # -- helpers ---------------------------------------------------------------------------------
    def key(self, key, pressed):
        if self.real:
            self.assertTrue(self.sim.control('realkey %d %d' % (key, pressed)).get('ok'))
        else:
            super().key(key, pressed)

    def until(self, cond, what, timeout=30.0):
        deadline = time.monotonic() + timeout
        while True:
            got = cond()
            if got:
                return got
            self.assertLess(time.monotonic(), deadline, 'never ' + what)
            self.sim.advance(10)

    def count(self, marker):
        return len(self.lines(marker))

    def record(self):
        path = self.root / 'xy-forever/settings.txt'
        return dict(line.split('=', 1) for line in path.read_text().splitlines() if '=' in line)

    def stored(self, key):
        """record()[key] while a save may be replacing the file (None in between)."""
        try:
            return self.record().get(key)
        except FileNotFoundError:
            return None

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

    def saved(self, seen):
        """Until a save's end since `seen` lines; still on the Settings page (nothing left)."""
        self.until(lambda: self.count(SAVED) > seen, 'the save')
        self.assertIn(self.lines('menu: screen ')[-1].split()[0], ('%d' % SETTINGS, '%d' % OPTIONS))   # never home

    def pick_saved(self, row, moves):
        """pick, then its save (on its own, the page kept); the option picked."""
        seen = self.count(SAVED)
        option = self.pick(row, moves)
        self.saved(seen)
        return option

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
        self.pick_saved(1, 1)                                             # Menu at power-on: saved at once
        self.assertEqual(self.record()['power_on'], 'always')
        self.press(MINUS)                                                 # last Settings row is Content packs
        self.press(PLAY)
        self.until(lambda: self.count('pack page 0: 7 entries, more=1'), 'first seven packs')
        first = self.lines('pack id: ')[-7:]
        for _ in range(7):
            self.press(PLUS)
        self.until(lambda: self.count('pack page 1: 2 entries, more=0'), 'last two packs')
        self.assertEqual(set(first + self.lines('pack id: ')[-2:]), ids)
        self.press(MINUS)
        self.until(lambda: self.count('pack page 0: 7 entries, more=1') >= 2, 'back to page one')
        self.assertEqual(self.lines('pack id: ')[-7:], first)
        self.press(STOP)
        self.assertEqual(self.record()['power_on'], 'always')

    def test_empty_pack_page(self):
        self.settings_page()
        self.press(MINUS)
        self.press(PLAY)
        self.until(lambda: self.count('pack page 0: 0 entries, more=0'), 'empty installed list')
        self.press(STOP)
        self.until(lambda: self.count('menu: screen %d row 3 ' % SETTINGS) >= 2, 'back to Settings')

    def test_home_has_no_info_row(self):
        self.until(lambda: self.lines('settings: confirm=0'), 'the settings read')
        self.press(MINUS)                                                  # wraps to the last row: Settings
        self.press(PLAY)
        self.until(lambda: self.lines('menu: screen %d row 3 ' % SETTINGS), 'Settings, the last of four rows')

    def test_menu_at_power_on_always_then_play_held(self):
        self.until(lambda: self.lines('settings: confirm=0 power_on=0 usb=0 newer=0'), 'the settings read')
        self.settings_page()
        self.assertEqual(self.pick_saved(1, 1), 1)                         # PLAY held -> Always
        self.assertEqual(self.record()['power_on'], 'always')
        self.assertEqual(self.record()['format'], '1')
        self.power(play=False)                                             # no PLAY: the menu opens anyway
        self.until(lambda: self.after_power_on('menu (home)'), 'the menu without PLAY')
        self.reconnect()                                                   # with USB, as with PLAY
        self.assertFalse(self.in_os())
        self.until(lambda: self.after_power_on('settings: confirm=0 power_on=1'), 'the record read back')
        self.settings_page()
        self.assertEqual(self.pick_saved(1, -1), 0)                        # Always -> PLAY held
        self.assertEqual(self.record()['power_on'], 'play')
        self.power(play=False)                                             # PLAY held: a plain power-on boots
        self.until(self.in_os, 'the OS')
        self.assertFalse(self.after_power_on('menu (home)'))

    # -- USB mode (#191) ------------------------------------------------------------------------
    def status(self):
        return self.dev.once(P.STATUS, timeout=15.0)[0]

    def hello_serial(self):
        return P.Hello(self.dev.hello()[1]).keys.get('serial')

    def mtp(self, c, code, *params):
        return c.transaction(code, *params)[0]

    def test_usb_modes_switch_live_in_one_boot(self):
        """Each mode applies at its pick, back and forth in one boot, on the same connection (never re-enumerated):
        MTP + Serial both; Serial: MTP operations refused (OpenSession too; GetDeviceInfo answers), serial works; MTP:
        every serial request but hello answers serial_off (the host tool says why), hello serial=off, MTP works;
        back to MTP + Serial: both. Each pick saved on its own; a power-on (PLAY held) reads the last one back."""
        self.until(lambda: self.lines('settings: confirm=0 power_on=0 usb=0'), 'the settings read')
        self.assertEqual(self.hello_serial(), 'on')
        c = TunnelClient(self.dev)
        self.assertEqual(c.tunnel(0)[0], ids.S_OK)                         # diag.mtp op 0: the session (no composite)
        self.assertEqual(self.mtp(c, OPEN_SESSION, 1), R_OK)
        self.assertEqual(self.mtp(c, GET_STORAGE_IDS), R_OK)
        self.settings_page()
        self.assertEqual(self.pick(2, -1), 2)                              # MTP + Serial -> Serial (- wraps)
        self.assertEqual(self.mtp(c, GET_STORAGE_IDS), ACCESS_DENIED)      # at once: the session ended
        self.assertEqual(self.mtp(c, OPEN_SESSION, 1), ACCESS_DENIED)      # and none opens
        self.assertEqual(self.mtp(c, GET_DEVICE_INFO), R_OK)
        self.assertEqual((self.status(), self.hello_serial()), (ids.S_OK, 'on'))
        self.until(lambda: self.stored('usb') == 'serial', 'saved')
        self.real = True                                                   # serial goes off: the OP-XY's own keys
        self.assertEqual(self.pick(2, -1), 1)                              # Serial -> MTP
        self.assertEqual(self.status(), ids.S_SERIAL_OFF)                  # at once, nothing done
        self.assertEqual(self.dev.once(ids.KEY, b'\0' * 8)[0], ids.S_SERIAL_OFF)   # the host's keys too
        said = []
        self.dev.on_ask = said.append
        self.assertEqual(self.hello_serial(), 'off')                       # hello still answers, and says why
        self.assertEqual(said, [host_trust.SERIAL_OFF])
        with self.assertRaises(host_trust.SerialOff) as refused:
            self.dev.call(P.STATUS)
        self.assertIn('Settings > USB mode', str(refused.exception))
        self.assertEqual(self.mtp(c, OPEN_SESSION, 1), R_OK)                # MTP is on again (the tunnel stays)
        self.assertEqual(self.mtp(c, GET_STORAGE_IDS), R_OK)
        self.until(lambda: self.stored('usb') == 'mtp_only', 'saved')
        self.assertEqual(self.pick(2, -1), 0)                              # MTP -> MTP + Serial
        self.until(lambda: self.status() == ids.S_OK, 'serial on again')
        self.assertEqual(self.hello_serial(), 'on')
        self.assertEqual(self.mtp(c, GET_STORAGE_IDS), R_OK)
        self.assertEqual(self.pick(2, -1), 2)                              # and to Serial once more
        self.assertEqual(self.mtp(c, GET_STORAGE_IDS), ACCESS_DENIED)
        self.assertEqual(self.status(), ids.S_OK)
        self.until(lambda: self.stored('usb') == 'serial', 'saved')
        self.assertEqual(self.count('USB on: listening'), 1)               # one USB start, never stopped
        self.assertFalse(self.lines('USB off') or self.lines('USB host detached') or self.lines('USB host replaced'))
        self.power(play=True)
        self.reconnect()
        self.until(lambda: self.after_power_on('settings: confirm=0 power_on=0 usb=2'), 'USB mode read back')

    def test_mtp_mode_at_power_on(self):
        """A record with usb=mtp_only: from the first request serial is off (hello says so); the OP-XY's own keys
        turn it back on without a restart."""
        (self.root / 'xy-forever/settings.txt').write_text('format=1\nconfirm=0\npower_on=play\nusb=mtp_only\n')
        self.power(play=True)
        self.reconnect()
        self.until(lambda: self.after_power_on('settings: confirm=0 power_on=0 usb=1'), 'the record read')
        self.assertEqual((self.hello_serial(), self.status()), ('off', ids.S_SERIAL_OFF))
        self.real = True
        self.settings_page()
        self.assertEqual(self.pick(2, -1), 0)                              # MTP -> MTP + Serial
        self.until(lambda: self.status() == ids.S_OK, 'serial on')
        self.assertEqual(self.hello_serial(), 'on')

    def test_about_page(self):
        self.until(lambda: self.lines('settings: confirm=0 power_on=0 usb=0'), 'the settings read')
        self.settings_page()
        for _ in range(3):                                                 # #65: Ask before USB control before About
            self.press(PLUS)
        seen = self.count('menu: info image: ')
        self.press(PLUS)                                                   # About
        self.press(PLAY)
        self.until(lambda: self.count('menu: info image: ') > seen, 'the About page')
        screens = self.count('menu: screen %d ' % SETTINGS)
        self.press(STOP)                                                   # back to Settings
        self.until(lambda: self.count('menu: screen %d ' % SETTINGS) > screens, 'back to Settings')

    def test_option_list_and_quick_picks_one_write(self):
        """The list opens on the value in effect; STOP leaves it unchanged; quick picks (two rows, well within the
        quiet moment) make one write of both, on the page (one read-write window, no catalog fetch); leaving
        afterwards writes nothing more."""
        self.until(lambda: self.lines('settings: confirm=0 power_on=0 usb=0 newer=0'), 'the settings read')
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
        windows, seen = self.sim.status()['rw_mounts'], self.count(SAVED)
        for k in (PLAY, PLUS, PLAY, PLUS, PLAY, MINUS, PLAY):              # Always; then USB mode: Serial (- wraps)
            self.press(k)
        self.saved(seen)
        self.assertEqual((self.record()['power_on'], self.record()['usb'], self.record()['format']), ('always', 'serial', '1'))
        self.assertEqual(self.sim.status()['rw_mounts'], windows + 1)       # one write, both values
        self.assertEqual(self.count(SAVED), seen + 1)
        self.press(STOP)                                                   # the home menu: nothing left to write
        self.until(lambda: self.lines('menu: screen ')[-1].startswith('%d ' % MAIN), 'the home menu')
        self.sim.advance(2000)
        self.assertEqual(self.sim.status()['rw_mounts'], windows + 1)


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
