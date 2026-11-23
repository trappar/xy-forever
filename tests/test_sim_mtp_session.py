"""The MTP session on processor B in the simulator (target/mtp_session, usb_service job_kind 4), without
real USB: the menu's MTP mode row starts it, the dev command diag.mtp (0x61) is the test transport, and the
scripted MTP client of test_mtp_responder.py drives it. Needs clang++ and the private stock 1.2.1 images
(skipped without them). No device access."""

import random
import shutil
import struct
import tempfile
import time
import unittest
from pathlib import Path

import pytest
import test_mtp_responder as R

from xy_forever import bounds, mtp_read, paths, sim
from xy_forever import usb_proto_ids as P
from xy_forever import xyf_host as host

UNAVAILABLE = sim.available() or (None if sim.STOCK_OS.is_file() else 'private stock 1.2.1 os.ldr absent')
TUNNEL = 0x61
PLAY, STOP, PLUS = 51, 52, 54
PASS_BOUND_MS = 250  # one request per pass (sim timing)


class TunnelClient(R.Client):
    """test_mtp_responder.Client over diag.mtp: out = op 2 (retried while B answers busy), take = op 3 until
    the response container is complete (the session steps the responder from B's menu loop meanwhile)."""

    def __init__(self, dev):
        self.dev, self.tid, self.zlps = dev, 0, 0

    def tunnel(self, op, body=b''):
        info, self.dev.hello_info = self.dev.hello_info, None  # diag.mtp is not in the hello list
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
            self.sim.advance(2)

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
                self.sim.advance(2)
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
        cls.work = Path(tempfile.mkdtemp(prefix='sim-mtp-session-', dir=paths.SESSIONS))  # evidence: private only
        cls.seeded = sim.seed(cls.work / 'seed')
        cls.big = random.Random(35).randbytes(200 * 1024 + 500)  # + the 12-byte header: a multiple of 512 (ZLP)
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
            self.sim.advance(20)

    def enter(self, n=1):
        # 188: no MTP mode row (the product enters the session when the composite starts); the sim has no
        # composite, so diag.mtp op 0 enters it.
        self.assertEqual(self.c.tunnel(0)[0], P.S_OK)
        self.assertEqual(self.c.state()['stage'], 1)

    def leave(self, n=1):
        self.assertEqual(self.c.tunnel(1)[0], P.S_OK)  # diag.mtp op 1
        deadline = time.monotonic() + 60
        while self.c.state()['busy']:
            self.assertLess(time.monotonic(), deadline, 'the session never ended')
            self.sim.advance(20)
        self.assertEqual(self.c.state()['stage'], 4)

    def file_command(self):
        return self.dev.call(P.LIST_DIR, b'/yaffs2/xy-forever\0' + struct.pack('<I', 0))[0]

    def opened(self):
        self.assertEqual(self.c.transaction(0x1002, 1)[0], R.OK)

    def folder(self, name):
        return self.c.named()[name]

    # -- tests
    @pytest.mark.sim
    def test_browse_download_upload_delete_and_busy(self):
        """Kept: MTP smoke test: browse, download, upload and delete through both processors."""
        self.assertEqual(self.file_command(), P.S_OK)
        self.enter()
        self.assertEqual(self.file_command(), P.S_OK)  # an open session holds nothing
        code, _, info = self.c.transaction(0x1001)
        self.assertEqual(code, R.OK)
        self.assertEqual(mtp_read.device_info(info)['serial'], 'SIM00001')  # the simulator's metadata serial (#288)
        self.opened()
        xyf = self.folder('xy-forever')
        names = self.c.named(xyf)
        self.assertIn('big.bin', names)
        self.assertIn('tmp', names)
        code, _, data = self.c.transaction(0x1009, names['big.bin'])
        self.assertEqual(code, R.OK)
        self.assertEqual(data, self.big)  # byte for byte
        self.assertGreaterEqual(self.c.zlps, 1)  # 12 + 200 KiB + 500: a ZLP after the data
        tmp = names['tmp']
        body = random.Random(7).randbytes(5000)
        code, params, _ = self.c.transaction(
            0x100C, R.STORAGE, tmp, data=R.object_info('up.bin', len(body), parent=tmp)
        )
        self.assertEqual(code, R.OK, hex(code))
        handle = params[2]
        self.assertEqual(self.c.transaction(0x100D, data=body)[0], R.OK)
        self.assertEqual((self.root / 'xy-forever/tmp/up.bin').read_bytes(), body)
        self.assertEqual(self.c.transaction(0x100B, handle)[0], R.OK)
        self.assertFalse((self.root / 'xy-forever/tmp/up.bin').exists())
        user = self.folder('user')  # the write rule: user/ is writable
        code, params, _ = self.c.transaction(0x100C, R.STORAGE, user, data=R.object_info('x.bin', 3, parent=user))
        self.assertEqual(code, R.OK)
        self.assertEqual(self.c.transaction(0x100D, data=b'abc')[0], R.OK)
        self.assertEqual((self.root / 'user/x.bin').read_bytes(), b'abc')
        self.assertEqual(self.c.transaction(0x100B, params[2])[0], R.OK)
        self.assertFalse((self.root / 'user/x.bin').exists())
        shutil.rmtree(self.root / 'xy-forever/tmp')  # #144: tmp/ gone: the open makes it again
        code, params, _ = self.c.transaction(0x100C, R.STORAGE, user, data=R.object_info('y.bin', 3, parent=user))
        self.assertEqual(code, R.OK)
        self.assertEqual(self.c.transaction(0x100D, data=b'def')[0], R.OK)
        self.assertEqual((self.root / 'user/y.bin').read_bytes(), b'def')
        self.assertTrue((self.root / 'xy-forever/tmp').is_dir())
        (self.root / 'upgrade').mkdir(exist_ok=True)
        inbox = self.folder('upgrade')
        code, _, _ = self.c.transaction(0x100C, R.STORAGE, inbox, data=R.object_info('x.tfw', 3, parent=inbox))
        self.assertEqual(code, R.ACCESS_DENIED)  # never in the stock updater's inbox
        self.assertFalse((self.root / 'upgrade/x.tfw').exists())
        s = self.c.state()
        self.assertLess(s['longest_ms'], PASS_BOUND_MS, s)
        self.assertEqual(s['link'], 0)
        self.assertIn('mtp screen: stage=1', self.log())
        self.leave()
        self.assertEqual(self.file_command(), P.S_OK)  # serial commands work again

    @pytest.mark.sim
    def test_a_silent_clean_error_b_alive(self):
        """Kept: MTP fault: a silent A gives a clean error and B stays alive."""
        self.enter()
        self.opened()
        xyf = self.folder('xy-forever')
        before = self.sim.status()['a_silenced']
        self.sim.set('a_silent', 1)
        self.c.out(self.c.command(0x1007, R.STORAGE, 0, xyf))  # a listing: B's next request is held
        deadline = time.monotonic() + 30.0
        while self.sim.status()['a_silenced'] == before:
            self.assertLess(time.monotonic(), deadline)
            self.sim.advance(5)
        for _ in range(3):
            self.sim.advance(6000)  # past the request's bound
        self.sim.set('a_silent', 0)
        answers = self.c.answers(self.c.take(timeout=bounds.WAIT_S))
        self.assertEqual(answers[-1][1], R.BUSY)  # a clean error (f_link: Device_Busy)
        self.assertEqual(self.c.state()['link'], 1)
        self.assertEqual(self.dev.hello()[0], 0)  # B alive
        self.assertEqual(self.dev.call(P.STATUS)[0], P.S_OK)
        self.leave()  # leaving still works cleanly
        self.assertEqual(self.file_command(), P.S_LINK)  # the link is down: B answers link
        # Entering needs nothing of A (the composite enables the session at its start); each operation that
        # needs the files checks A first and answers Device_Busy while A is gone (the link above).


if __name__ == '__main__':
    unittest.main()
