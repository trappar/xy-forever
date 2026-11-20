"""Ask before USB control (#65) in the simulator: with the setting on, the first serial request but hello of a
connection makes the device ask "Allow this computer to control your OP-XY?" (no time limit); meanwhile every
request but hello answers `waiting` at once (nothing held, nothing done) and hello says trust=asking. A real PLAY
(control "realkey", the PSoC's keys) trusts the computer until the cable is unplugged (control "unplug": a bus reset)
or the device restarts, across closing and reopening the port; a real STOP refuses every request (`declined`) as
long. Injected keys (diag.key) never answer, and never change the setting: only the OP-XY's own keys do. MTP stays
open (the serial tunnel, as its own interface). Needs clang++ and the private stock 1.2.1 images (skipped without
them). No device access."""
from pathlib import Path
import shutil
import struct
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
import xyf_host as host                                                    # noqa: E402
from xy_forever import host_trust, sim, usb_proto as P, usb_proto_ids as ids   # noqa: E402
import test_mtp_responder as R                                            # noqa: E402
from test_sim_mtp_session import TunnelClient                             # noqa: E402

UNAVAILABLE = sim.available() or (None if sim.STOCK_OS.is_file() else 'private stock 1.2.1 os.ldr absent')
XYF = '/yaffs2/xy-forever/'
PLAY, STOP, MINUS = 51, 52, 53                                             # xyf_menu.hpp Key
ON = b'format=1\nconfirm=1\n'
ENTRY = b'opxy-121-stock\0'
OLD_OS, NEW_OS = b'a stand-in os.ldr', b'another stand-in os.ldr'
# Home: Settings (MINUS wraps to it), its page; up to Ask before USB control (Mod memory, About, it); its options
# (On); Off; picked; back (saved when the page is left).
TO_OFF = (MINUS, PLAY, MINUS, MINUS, PLAY, MINUS, PLAY, STOP)


@unittest.skipIf(UNAVAILABLE, UNAVAILABLE)
class AskTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='xyf-ask-')
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
        self.sim = sim.Simulator(self.root, self.dir / 'bootpart.bin', build_dir=self.out, log=self.dir / 'log.txt', ask=True).start()
        self.dev = None
        self.connect()

    def tearDown(self):
        self.dev.t.close()
        self.sim.stop()

    # -- helpers ---------------------------------------------------------------------------
    def connect(self):
        """The port (re)opened, and hello."""
        if self.dev:
            self.dev.t.close()
        self.dev = host.Device(host.TcpPort(self.sim.address), on_log=None)
        self.assertEqual(self.dev.hello()[0], 0)

    def lines(self, marker):
        log = (self.dir / 'log.txt').read_text(errors='replace') if (self.dir / 'log.txt').exists() else ''
        return [line[line.index(marker) + len(marker):] for line in log.splitlines() if marker in line]

    def until(self, cond, what, timeout=30.0):
        deadline = time.monotonic() + timeout
        while not cond():
            self.assertLess(time.monotonic(), deadline, 'never ' + what)
            time.sleep(0.01)

    def keys(self):
        """hello's confirm= and trust=."""
        keys = P.Hello(self.dev.hello()[1]).keys
        return keys.get('confirm'), keys.get('trust')

    def once(self, kind, payload=b''):
        """One request, its status (no retry while the device asks)."""
        return self.dev.once(kind, payload, timeout=15.0)[0]

    def ask(self):
        """The first request of the connection opens the question (busy while the settings are still read)."""
        self.until(lambda: self.once(P.STATUS) == ids.S_WAITING, 'asked')
        self.assertEqual(self.keys(), ('1', 'asking'))

    def real(self, *keys):
        for key in keys:
            for pressed in (1, 0):
                self.assertTrue(self.sim.control('realkey %d %d' % (key, pressed)).get('ok'))

    def injected(self, *keys):
        for key in keys:
            for pressed in (1, 0):
                self.assertEqual(self.once(ids.KEY, struct.pack('<II', key, pressed)), 0)

    def answered(self, trust):
        self.until(lambda: self.keys()[1] == trust, 'trust=' + trust)

    def slots(self):
        return b''.join((self.root / 'xy-forever' / n).read_bytes() for n in ('state-a.txt', 'state-b.txt')
                        if (self.root / 'xy-forever' / n).exists())

    # -- tests -----------------------------------------------------------------------------
    def test_allowed(self):
        self.assertEqual(self.keys(), ('1', 'none'))                       # hello alone asks nothing
        self.assertFalse(self.lines('trust: asks'))
        self.ask()
        self.real(PLAY)
        self.answered('allowed')
        self.assertEqual(self.once(P.STATUS), 0)
        self.assertEqual(self.dev.set_default('opxy-121-stock')[0], 0)    # runs at once: no question per command
        self.assertIn(b'default=opxy-121-stock', self.slots())
        self.assertEqual(self.lines('trust: asks'), [''])

    def test_not_allowed(self):
        before = self.slots()
        self.ask()
        self.real(STOP)
        self.answered('refused')
        for kind, payload in ((P.STATUS, b''), (P.SET_DEFAULT, ENTRY), (P.READ_FILE, (XYF + 'state-a.txt\0').encode())):
            self.assertEqual(self.once(kind, payload), ids.S_DECLINED, kind)
        with self.assertRaises(host_trust.NotAllowed):                     # xyf_host stops there
            self.dev.call(P.STATUS)
        self.assertEqual(self.slots(), before)
        self.connect()                                                     # the port again: still refused
        self.assertEqual(self.once(P.STATUS), ids.S_DECLINED)

    def test_waiting_while_asking(self):
        before = self.slots()
        self.ask()
        for kind, payload in ((P.STATUS, b''), (P.SET_DEFAULT, ENTRY), (P.READ_FILE, (XYF + 'state-a.txt\0').encode()),
                              (ids.SCREEN, b''), (ids.WRITE_BEGIN, b'')):
            start = time.monotonic()
            self.assertEqual(self.once(kind, payload), ids.S_WAITING, kind)
            self.assertLess(time.monotonic() - start, 5.0)                # at once: nothing held open
        self.assertEqual(self.keys(), ('1', 'asking'))
        self.assertEqual(self.slots(), before)
        self.assertEqual(self.lines('trust: asks'), [''])                 # asked once
        self.sim.advance(120000)                                           # no time limit
        self.assertEqual(self.keys(), ('1', 'asking'))
        said = []
        self.dev.on_ask = said.append                                      # xyf_host: says so once, sends again
        allow = threading.Thread(target=lambda: (self.until(lambda: said, 'the host waiting'), self.real(PLAY)))
        allow.start()
        code, _ = self.dev.call(P.STATUS)
        allow.join(30.0)
        self.assertEqual((code, said), (0, [host_trust.ASK]))

    def test_trust_kept_across_port_close_and_reopen(self):
        self.ask()
        self.real(PLAY)
        self.answered('allowed')
        self.connect()
        self.assertEqual(self.keys(), ('1', 'allowed'))
        self.assertEqual(self.once(P.STATUS), 0)
        self.assertEqual(self.lines('trust: asks'), [''])

    def test_trust_ended_by_unplugging(self):
        for answer, trust in ((PLAY, 'allowed'), (STOP, 'refused')):
            with self.subTest(trust=trust):
                self.ask()
                self.real(answer)
                self.answered(trust)
                self.assertTrue(self.sim.control('unplug').get('ok'))
                self.until(lambda: self.lines('unplugged (a bus reset)'), 'the connection closed')
                self.connect()
                self.assertEqual(self.keys(), ('1', 'none'))
                self.ask()                                                  # asked again
        self.assertEqual(len(self.lines('trust: asks')), 3)

    def test_injected_keys_are_ignored(self):
        self.ask()
        for key in (PLAY, STOP):
            for pressed in (1, 0):
                self.assertEqual(self.once(ids.KEY, struct.pack('<II', key, pressed)), ids.S_WAITING)
        self.assertFalse(self.lines('trust: answer'))
        self.assertEqual(self.keys(), ('1', 'asking'))
        self.real(PLAY)
        self.answered('allowed')
        self.assertTrue(all(line.endswith('(real)') for line in self.lines('trust: key ')))

    def test_setting_not_changeable_over_usb(self):
        record = self.root / 'xy-forever/settings.txt'
        self.ask()
        self.real(PLAY)
        self.answered('allowed')
        self.injected(*TO_OFF[:5])                                         # the host's keys: the row refuses
        self.until(lambda: self.lines("settings: play on row 3: note=Ask before USB control changes only"), 'the note')
        self.injected(STOP)                                                # home, on Settings
        self.assertEqual(record.read_bytes(), ON)
        self.assertEqual(self.keys(), ('1', 'allowed'))
        self.real(PLAY, PLAY, MINUS, PLAY, STOP)                           # own keys (the page kept its row): Off
        self.until(lambda: self.lines('menu: line: Settings saved.'), 'the save')
        self.assertIn(b'confirm=0\n', record.read_bytes())
        self.assertEqual(self.keys()[0], '0')

    def test_setting_off_asks_nothing(self):
        self.ask()
        self.real(PLAY)
        self.answered('allowed')
        self.real(*TO_OFF)
        self.until(lambda: self.lines('menu: line: Settings saved.'), 'the save')
        self.assertTrue(self.sim.control('unplug').get('ok'))
        self.until(lambda: self.lines('unplugged (a bus reset)'), 'the connection closed')
        self.connect()
        self.assertEqual(self.keys(), ('0', 'none'))
        self.assertEqual(self.once(P.STATUS), 0)
        self.assertEqual(len(self.lines('trust: asks')), 1)

    def test_mtp_stays_open(self):
        """MTP (the serial tunnel, as its own interface), the setting on and nothing asked: os.ldr overwritten
        (SendObject onto its name), xy-forever/ deleted (DeleteObject, deepest first), settings.txt with it."""
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
        self.assertFalse(self.lines('trust: asks'))
        self.assertEqual(self.keys(), ('1', 'none'))


if __name__ == '__main__':
    unittest.main()
