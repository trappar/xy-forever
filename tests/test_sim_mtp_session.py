"""The MTP session on processor B in the simulator (target/mtp_session, usb_service job_kind 4), without
real USB: the menu's MTP mode row starts it, the dev command diag.mtp (0x61) is the test transport, and the
scripted MTP client of test_mtp_responder.py drives it. Needs clang++ and the private stock 1.2.1 images
(skipped without them). No device access."""
from pathlib import Path
import random
import shutil
import struct
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
import xyf_host as host                                                   # noqa: E402
from xy_forever import paths, sim, usb_proto_ids as P                    # noqa: E402
import test_mtp_responder as R                                            # noqa: E402

UNAVAILABLE = sim.available() or (None if sim.STOCK_OS.is_file() else 'private stock 1.2.1 os.ldr absent')
TUNNEL = 0x61
PLAY, STOP, PLUS = 51, 52, 54
PASS_BOUND_MS = 250                                                       # one request per pass (sim timing)


class TunnelClient(R.Client):
    """test_mtp_responder.Client over diag.mtp: out = op 2 (retried while B answers busy), take = op 3 until
    the response container is complete (the session steps the responder from B's menu loop meanwhile)."""

    def __init__(self, dev):
        self.dev, self.tid, self.zlps = dev, 0, 0

    def tunnel(self, op, body=b''):
        info, self.dev.hello_info = self.dev.hello_info, None             # diag.mtp is not in the hello list
        try:
            return self.dev.call(TUNNEL, bytes([op]) + body, timeout=10.0)
        finally:
            self.dev.hello_info = info

    def out(self, transfer, timeout=20.0):
        deadline = time.monotonic() + timeout
        while True:
            code, _ = self.tunnel(2, transfer)
            if code == P.S_OK:
                return
            assert code == P.S_BUSY and time.monotonic() < deadline, 'transfer not taken: %d' % code
            time.sleep(0.002)

    def take(self, most=1 << 30, timeout=30.0):
        stream, deadline = b'', time.monotonic() + timeout
        while True:
            at, done = 0, False
            while at + 12 <= len(stream):
                size, kind = struct.unpack_from('<IH', stream, at)
                if at + size > len(stream):
                    break
                at += size
                done = kind == 3
            if done and at == len(stream):
                return stream
            assert time.monotonic() < deadline, 'no response container: %r' % stream[:64]
            code, body = self.tunnel(3, struct.pack('<I', 4096))
            assert code == P.S_OK, code
            self.zlps += struct.unpack_from('<I', body)[0]
            if len(body) == 4:
                time.sleep(0.002)
            stream += body[4:]

    def state(self):
        code, body = self.tunnel(5)
        assert code == P.S_OK
        keys = ('stage', 'bytes_in', 'bytes_out', 'longest_ms', 'device_status', 'link', 'steps', 'requests', 'busy')
        return dict(zip(keys, struct.unpack_from('<9I', body)))


@unittest.skipIf(UNAVAILABLE, UNAVAILABLE)
class MtpSessionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        paths.SESSIONS.mkdir(parents=True, exist_ok=True)
        cls.work = Path(tempfile.mkdtemp(prefix='sim-mtp-session-', dir=paths.SESSIONS))   # evidence: private only
        cls.seeded = sim.seed(cls.work / 'seed')
        cls.big = random.Random(35).randbytes(200 * 1024 + 500)            # + the 12-byte header: a multiple of 512 (ZLP)
        (cls.seeded / 'xy-forever/big.bin').write_bytes(cls.big)
        (cls.seeded / 'xy-forever/tmp').mkdir(parents=True, exist_ok=True)
        (cls.seeded / 'user').mkdir(exist_ok=True)
        cls.runs = 0

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.work, ignore_errors=True)

    def setUp(self):
        type(self).runs += 1
        self.dir = self.work / ('run-%d' % self.runs)
        self.root = self.dir / 'yaffs2'
        shutil.copytree(self.seeded, self.root)
        self.sim = sim.Simulator(self.root, self.dir / 'bootpart.bin', log=self.dir / 'log.txt').start()
        self.dev = host.Device(host.TcpPort(self.sim.address), on_log=None)
        self.assertEqual(self.dev.hello()[0], 0)
        self.c = TunnelClient(self.dev)

    def tearDown(self):
        self.dev.t.close()
        self.sim.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    # -- helpers
    def log(self):
        return (self.dir / 'log.txt').read_text(errors='replace') if (self.dir / 'log.txt').exists() else ''

    def key(self, k):
        for pressed in (1, 0):
            self.assertEqual(self.dev.call(P.KEY, struct.pack('<II', k, pressed))[0], 0)

    def wait_log(self, text, count=1, timeout=30.0):
        deadline = time.monotonic() + timeout
        while self.log().count(text) < count:
            self.assertLess(time.monotonic(), deadline, 'never logged %r: %s' % (text, self.log()[-3000:]))
            time.sleep(0.02)

    def enter(self, n=1):
        # 188: no MTP mode row (the product enters the session when the composite starts); the sim has no
        # composite, so diag.mtp op 0 enters it.
        self.assertEqual(self.c.tunnel(0)[0], P.S_OK)
        self.assertEqual(self.c.state()['stage'], 1)

    def leave(self, n=1):
        self.assertEqual(self.c.tunnel(1)[0], P.S_OK)                     # diag.mtp op 1
        deadline = time.monotonic() + 60
        while self.c.state()['busy']:
            self.assertLess(time.monotonic(), deadline, 'the session never ended')
            time.sleep(0.02)
        self.assertEqual(self.c.state()['stage'], 4)

    def file_command(self):
        return self.dev.call(P.LIST_DIR, b'/yaffs2/xy-forever\0' + struct.pack('<I', 0))[0]

    def opened(self):
        self.assertEqual(self.c.transaction(0x1002, 1)[0], R.OK)

    def folder(self, name):
        return self.c.named()[name]

    # -- tests
    def test_browse_download_upload_delete_and_busy(self):
        self.assertEqual(self.file_command(), P.S_OK)
        self.enter()
        self.assertEqual(self.file_command(), P.S_OK)                    # an open session holds nothing
        code, _, info = self.c.transaction(0x1001)
        self.assertEqual(code, R.OK)
        self.assertTrue(info)
        self.opened()
        xyf = self.folder('xy-forever')
        names = self.c.named(xyf)
        self.assertIn('big.bin', names)
        self.assertIn('tmp', names)
        code, _, data = self.c.transaction(0x1009, names['big.bin'])
        self.assertEqual(code, R.OK)
        self.assertEqual(data, self.big)                                   # byte for byte
        self.assertGreaterEqual(self.c.zlps, 1)                            # 12 + 200 KiB + 500: a ZLP after the data
        tmp = names['tmp']
        body = random.Random(7).randbytes(5000)
        code, params, _ = self.c.transaction(0x100C, R.STORAGE, tmp, data=R.object_info('up.bin', len(body), parent=tmp))
        self.assertEqual(code, R.OK, hex(code))
        handle = params[2]
        self.assertEqual(self.c.transaction(0x100D, data=body)[0], R.OK)
        self.assertEqual((self.root / 'xy-forever/tmp/up.bin').read_bytes(), body)
        self.assertEqual(self.c.transaction(0x100B, handle)[0], R.OK)
        self.assertFalse((self.root / 'xy-forever/tmp/up.bin').exists())
        user = self.folder('user')                                         # the write rule: user/ is writable
        code, params, _ = self.c.transaction(0x100C, R.STORAGE, user, data=R.object_info('x.bin', 3, parent=user))
        self.assertEqual(code, R.OK)
        self.assertEqual(self.c.transaction(0x100D, data=b'abc')[0], R.OK)
        self.assertEqual((self.root / 'user/x.bin').read_bytes(), b'abc')
        self.assertEqual(self.c.transaction(0x100B, params[2])[0], R.OK)
        self.assertFalse((self.root / 'user/x.bin').exists())
        (self.root / 'upgrade').mkdir(exist_ok=True)
        inbox = self.folder('upgrade')
        code, _, _ = self.c.transaction(0x100C, R.STORAGE, inbox, data=R.object_info('x.tfw', 3, parent=inbox))
        self.assertEqual(code, R.ACCESS_DENIED)                            # never in the stock updater's inbox
        self.assertFalse((self.root / 'upgrade/x.tfw').exists())
        s = self.c.state()
        self.assertLess(s['longest_ms'], PASS_BOUND_MS, s)
        self.assertEqual(s['link'], 0)
        self.assertIn('mtp screen: stage=1', self.log())
        self.leave()
        self.assertEqual(self.file_command(), P.S_OK)                      # serial commands work again

    def test_depth_rule(self):
        """#124: SendObjectInfo makes a file in the 6th folder below /yaffs2 but no folder there (Access_Denied, nothing
        made, as for a file in a 7th folder made by hand); an older deep file still deletes."""
        deep = self.root / 'xy-forever/wt/a/b/c/d'
        (deep / 'f').mkdir(parents=True)
        (deep / 'f' / 'old.txt').write_bytes(b'old')                      # 7 folders deep: made before the rule
        self.enter()
        self.opened()
        handle = self.folder('xy-forever')
        for name in ('wt', 'a', 'b', 'c', 'd'):
            handle = self.c.named(handle)[name]
        code, params, _ = self.c.transaction(0x100C, R.STORAGE, handle, data=R.object_info('x.txt', 3, parent=handle))
        self.assertEqual(code, R.OK)
        self.assertEqual(self.c.transaction(0x100D, data=b'abc')[0], R.OK)
        self.assertEqual((deep / 'x.txt').read_bytes(), b'abc')
        info = R.object_info('g', fmt=R.FOLDER, parent=handle)
        self.assertEqual(self.c.transaction(0x100C, R.STORAGE, handle, data=info)[0], R.ACCESS_DENIED)
        self.assertFalse((deep / 'g').exists())
        f = self.c.named(handle)['f']
        info = R.object_info('y.txt', 3, parent=f)
        self.assertEqual(self.c.transaction(0x100C, R.STORAGE, f, data=info)[0], R.ACCESS_DENIED)
        self.assertEqual(self.c.transaction(0x100B, self.c.named(f)['old.txt'])[0], R.OK)
        self.assertFalse((deep / 'f' / 'old.txt').exists())
        self.leave()

    def test_cancel_mid_transfer_then_continue(self):
        self.enter()
        self.opened()
        names = self.c.named(self.folder('xy-forever'))
        self.c.out(self.c.command(0x1009, names['big.bin']))
        code, body = self.c.tunnel(3, struct.pack('<I', 4096))
        self.assertEqual(code, P.S_OK)
        self.assertEqual(self.file_command(), P.S_BUSY)                  # a transfer in flight holds the files
        self.assertEqual(self.c.tunnel(4)[0], P.S_OK)                      # class request Cancel
        deadline = time.monotonic() + 20
        while self.c.state()['device_status'] != R.OK:
            self.assertLess(time.monotonic(), deadline, 'still busy after cancel')
            self.c.tunnel(3, struct.pack('<I', 4096))
            time.sleep(0.01)
        self.c.tunnel(3, struct.pack('<I', 4096))                          # nothing left of the old transfer
        self.assertEqual(self.file_command(), P.S_OK)                    # the cancel freed them
        code, _, data = self.c.transaction(0x1009, names['big.bin'])
        self.assertEqual((code, data), (R.OK, self.big))
        self.leave()
        self.assertEqual(self.file_command(), P.S_OK)

    def test_a_silent_clean_error_b_alive(self):
        self.enter()
        self.opened()
        xyf = self.folder('xy-forever')
        before = self.sim.status()['a_silenced']
        self.sim.set('a_silent', 1)
        self.c.out(self.c.command(0x1007, R.STORAGE, 0, xyf))            # a listing: B's next request is held
        deadline = time.monotonic() + 30.0
        while self.sim.status()['a_silenced'] == before:
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.005)
        for _ in range(3):
            self.sim.advance(6000)                              # past the request's bound
        self.sim.set('a_silent', 0)
        answers = self.c.answers(self.c.take(timeout=60))
        self.assertEqual(answers[-1][1], R.BUSY)                           # a clean error (f_link: Device_Busy)
        self.assertEqual(self.c.state()['link'], 1)
        self.assertEqual(self.dev.hello()[0], 0)                           # B alive
        self.assertEqual(self.dev.call(P.STATUS)[0], P.S_OK)
        self.leave()                                                       # leaving still works cleanly
        self.assertEqual(self.file_command(), P.S_LINK)                    # the link is down: B answers link
        # Entering needs nothing of A (the composite enables the session at its start); each operation that
        # needs the files checks A first and answers Device_Busy while A is gone (the link above).

    def test_leave_and_reenter_twice(self):
        for n in (1, 2, 3):
            self.enter(n)
            self.opened()
            self.assertIn('xy-forever', self.c.named())
            self.leave(n)
            self.assertEqual(self.file_command(), P.S_OK)


if __name__ == '__main__':
    unittest.main()
