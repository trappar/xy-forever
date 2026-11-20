"""Confirm on device (#65) in the simulator: with the setting on, boot.install, boot.uninstall, library.set_default
and mods.apply wait for a real PLAY (control "realkey", the PSoC's keys) before they run; a real STOP or no answer
within 30 s is `declined` with nothing done; injected keys (diag.key) never answer; other requests are busy
meanwhile. HELLO reports the setting (confirm=). No USB command changes the setting: the Settings row does. File
transfers stay open (the gate covers commands, not files): MTP and the serial file commands overwrite /yaffs2/os.ldr
and delete xy-forever/, settings.txt with it, nothing asked. Needs clang++ and the private stock 1.2.1 images
(skipped without them). No device access."""
import os
from pathlib import Path
import shutil
import struct
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
import xyf_host as host                                                    # noqa: E402
from xy_forever import host_write, sim, usb_proto as P, usb_proto_ids as ids   # noqa: E402
from xy_forever.startup_ready import wait_startup_ready                    # noqa: E402
import test_mtp_responder as R                                            # noqa: E402
from test_sim_mtp_session import TunnelClient                             # noqa: E402

UNAVAILABLE = sim.available() or (None if sim.STOCK_OS.is_file() else 'private stock 1.2.1 os.ldr absent')
XYF = '/yaffs2/xy-forever/'
PLAY, STOP, MINUS = 51, 52, 53                                             # xyf_menu.hpp Key
ON = b'format=1\nconfirm=1\n'
ENTRY = b'opxy-121-stock\0'
OLD_OS, NEW_OS = b'a stand-in os.ldr', b'another stand-in os.ldr'


@unittest.skipIf(UNAVAILABLE, UNAVAILABLE)
class ConfirmTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='xyf-confirm-')
        cls.out = sim.build()
        cls.seeded = sim.seed(Path(cls.tmp.name) / 'seed')
        (cls.seeded / 'xy-forever/settings.txt').write_bytes(ON)          # the product default: on
        (cls.seeded / 'xy-forever/settings.old').write_bytes(ON)
        (cls.seeded / 'os.ldr').write_bytes(OLD_OS)
        cls.runs = 0

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        type(self).runs += 1
        self.dir = Path(self.tmp.name) / ('run-%d' % self.runs)
        self.root = self.dir / 'yaffs2'
        shutil.copytree(self.seeded, self.root)
        self.sim = sim.Simulator(self.root, self.dir / 'bootpart.bin', build_dir=self.out, log=self.dir / 'log.txt').start()
        self.dev = host.Device(host.TcpPort(self.sim.address), on_log=None)
        code, text = self.dev.hello()
        self.assertEqual(code, 0)
        wait_startup_ready(text, lambda remaining: self.dev.call(ids.STORAGE_SPACE, struct.pack('<I', 1),
                                                                 timeout=remaining)[0])

    def tearDown(self):
        self.dev.t.close()
        self.sim.stop()

    # -- helpers ---------------------------------------------------------------------------
    def lines(self, marker):
        log = (self.dir / 'log.txt').read_text(errors='replace') if (self.dir / 'log.txt').exists() else ''
        return [line[line.index(marker) + len(marker):] for line in log.splitlines() if marker in line]

    def until(self, cond, what, timeout=30.0):
        deadline = time.monotonic() + timeout
        while not cond():
            self.assertLess(time.monotonic(), deadline, 'never ' + what)
            time.sleep(0.01)

    def send(self, kind, payload):
        """A request without waiting for its reply (the device holds it while it asks)."""
        self.dev.seq = (self.dev.seq + 1) & 0xff
        self.dev.t.write(P.encode(kind, self.dev.seq, payload))
        return kind, self.dev.seq

    def reply(self, sent, timeout=30.0):
        kind, seq = sent
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for k, s, body in self.dev.parser.feed(self.dev.t.read(0.2)):
                if k == kind | P.REPLY and s == seq:
                    return struct.unpack_from('<I', body)[0], body[4:]
        self.fail('no reply to 0x%02x' % kind)

    def ask(self, kind, payload, what):
        """Sends a gated request until the device asks about it (busy, as a host retries, while the write
        window of the start-up reads still closes)."""
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            n = len(self.lines('confirm: asks: '))
            sent = self.send(kind, payload)
            until = time.monotonic() + 2.0
            while time.monotonic() < until:
                if len(self.lines('confirm: asks: ')) > n:
                    self.assertTrue(self.lines('confirm: asks: ')[-1].startswith(what), self.lines('confirm: asks: ')[-1])
                    return sent
                frames = [f for f in self.dev.parser.feed(self.dev.t.read(0.05)) if f[0] == kind | P.REPLY and f[1] == sent[1]]
                if frames:
                    self.assertEqual(struct.unpack_from('<I', frames[0][2])[0], ids.S_BUSY)
                    time.sleep(0.1)
                    break
        self.fail('never asked about 0x%02x' % kind)

    def real(self, key):
        for pressed in (1, 0):
            self.assertTrue(self.sim.control('realkey %d %d' % (key, pressed)).get('ok'))

    def slots(self):
        return b''.join((self.root / 'xy-forever' / n).read_bytes() for n in ('state-a.txt', 'state-b.txt')
                        if (self.root / 'xy-forever' / n).exists())

    def confirm_key(self):
        return P.Hello(self.dev.hello()[1]).keys.get('confirm')

    # -- tests -----------------------------------------------------------------------------
    def test_confirmed_runs_the_request(self):
        self.assertEqual(self.confirm_key(), '1')
        sent = self.ask(P.SET_DEFAULT, ENTRY, 'Set the default firmware; opxy-121-stock')
        self.assertNotIn(b'default=opxy-121-stock', self.slots())
        self.real(PLAY)
        code, body = self.reply(sent)
        self.assertEqual(code, 0, P.status_name(code))
        self.assertEqual(self.lines('confirm: answer ')[-1], 'yes')
        self.assertIn(b'default=opxy-121-stock', self.slots())

    def test_declined_changes_nothing(self):
        before = self.slots()
        sent = self.ask(P.SET_DEFAULT, ENTRY, 'Set the default firmware')
        self.assertEqual(self.dev.call(P.READ_FILE, (XYF + 'state-a.txt\0').encode())[0], ids.S_BUSY)   # one at a time
        self.real(STOP)
        self.assertEqual(self.reply(sent), (ids.S_DECLINED, bytes(4)))     # id_hash 0
        self.assertEqual(self.slots(), before)
        # Each gated command: declined with every reply field 0 (STOP, or nobody within 30 s).
        for kind, payload, size, what in ((P.INSTALL, bytes(32), 12, 'Install'), (P.UNINSTALL, bytes(32), 12, 'Uninstall'),
                                          (ids.MODS_APPLY, bytes(47), 8, 'Build mod firmware')):
            with self.subTest(kind=kind):
                sent = self.ask(kind, payload, what)
                if kind == ids.MODS_APPLY:
                    self.sim.advance(31000)                                 # no answer: declined at the bound
                else:
                    self.real(STOP)
                self.assertEqual(self.reply(sent), (ids.S_DECLINED, bytes(size)))
        self.assertEqual(self.lines('confirm: answer '), ['no', 'no', 'no', 'timed out'])
        self.assertEqual(self.dev.read_file(XYF + 'state-a.txt')[0], 0)    # answered again

    def test_injected_keys_are_ignored(self):
        before = self.slots()
        sent = self.ask(P.SET_DEFAULT, ENTRY, 'Set the default firmware')
        for key in (PLAY, STOP):
            for pressed in (1, 0):
                self.assertEqual(self.dev.call(P.KEY, struct.pack('<II', key, pressed))[0], 0)
        self.until(lambda: len(self.lines('confirm: key ')) >= 4, 'the injected keys')
        self.assertTrue(all(line.endswith('(injected)') for line in self.lines('confirm: key ')))
        self.assertFalse(self.lines('confirm: answer '))                   # still asking
        self.assertEqual(self.slots(), before)
        self.real(STOP)
        self.assertEqual(self.reply(sent)[0], ids.S_DECLINED)

    def test_setting_changes_on_the_device(self):
        record = self.root / 'xy-forever/settings.txt'
        self.assertEqual(self.confirm_key(), '1')
        # On the device: Settings > Confirm on device > Off, saved when the page is left.
        for key in (MINUS, PLAY, MINUS, MINUS, PLAY, MINUS, PLAY, STOP):   # home: Settings; its row; Off; back
            self.assertEqual(self.dev.call(P.KEY, struct.pack('<II', key, 1))[0], 0)
            self.assertEqual(self.dev.call(P.KEY, struct.pack('<II', key, 0))[0], 0)
        self.until(lambda: self.lines('menu: line: Settings saved.'), 'the save')
        self.assertIn(b'confirm=0\n', record.read_bytes())
        self.assertEqual(self.confirm_key(), '0')
        self.assertEqual(self.dev.set_default('opxy-121-stock')[0], 0)    # runs at once
        self.assertFalse(self.lines('confirm: asks: '))

    def test_file_commands_stay_open(self):
        """The serial file commands, the setting on: os.ldr overwritten, settings.txt replaced, xy-forever/ deleted
        (file.delete and file.rmdir, deepest first), nothing asked."""
        self.assertEqual(host_write.write_stream(self.dev, '/yaffs2/os.ldr', NEW_OS)[0], 0)
        self.assertEqual((self.root / 'os.ldr').read_bytes(), NEW_OS)
        self.assertEqual(host_write.write_stream(self.dev, XYF + 'settings.txt', b'format=1\nconfirm=0\n')[0], 0)
        for folder, _, files in os.walk(self.root / 'xy-forever', topdown=False):
            where = '/yaffs2/' + Path(folder).relative_to(self.root).as_posix()
            for name in files:
                self.assertEqual(self.dev.change(ids.DELETE_FILE, where + '/' + name), (0, 0), where + '/' + name)
            self.assertEqual(self.dev.change(ids.REMOVE_DIR, where), (0, 0), where)
        self.assertFalse((self.root / 'xy-forever').exists())
        self.assertFalse(self.lines('confirm: asks: '))

    def test_mtp_stays_open(self):
        """MTP, the setting on: os.ldr overwritten (SendObject onto its name), xy-forever/ deleted (DeleteObject,
        deepest first), nothing asked."""
        c = TunnelClient(self.dev)
        self.assertEqual(c.tunnel(0)[0], ids.S_OK)                        # diag.mtp op 0: the session (no composite)
        self.assertEqual(c.transaction(0x1002, 1)[0], R.OK)
        code, _, _ = c.transaction(0x100C, R.STORAGE, R.ROOT_PARENT, data=R.object_info('os.ldr', len(NEW_OS)))
        self.assertEqual(code, R.OK, hex(code))
        self.assertEqual(c.transaction(0x100D, data=NEW_OS)[0], R.OK)
        self.assertEqual((self.root / 'os.ldr').read_bytes(), NEW_OS)

        def remove(handle):
            if c.info(handle)['format'] == R.FOLDER:
                for child in c.handles(handle):
                    remove(child)
            self.assertEqual(c.transaction(0x100B, handle)[0], R.OK)
        remove(c.named()['xy-forever'])
        self.assertFalse((self.root / 'xy-forever').exists())
        self.assertFalse(self.lines('confirm: asks: '))

if __name__ == '__main__':
    unittest.main()
