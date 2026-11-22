"""Phase-1 A staging: production transport, independent RAM, admission and no-write evidence."""

import hashlib
import struct
import unittest
import zlib

import pytest
import test_simulator as T

from xy_forever import a_rehearsal as R
from xy_forever import sim
from xy_forever import usb_proto_ids as P

HANDLE, MAGIC = 0x41535447, 0x31534941


def envelope(op, token=100, offset=0, count=0, crc=0, kind=0, data=b'', version=1):
    return struct.pack('<8I', MAGIC, version, op, token, offset, count, crc, kind) + data


@unittest.skipIf(T.UNAVAILABLE, T.UNAVAILABLE)
class AStagingTests(unittest.TestCase):
    setUpClass = classmethod(T.SimulatorTest.setUpClass.__func__)
    tearDownClass = classmethod(T.SimulatorTest.tearDownClass.__func__)
    setUp = T.SimulatorTest.setUp
    tearDown = T.SimulatorTest.tearDown
    connect = T.SimulatorTest.connect
    wait = T.SimulatorTest.wait
    restarted = T.SimulatorTest.restarted

    def ready(self):
        self.dev = self.connect(startup_ready=True)
        self.stock = sim.STOCK_OS.read_bytes()
        return self.dev

    def progress(self):
        code, state = R.call(self.dev, 1)
        self.assertEqual(code, 0)
        return state

    def wait_done(self):
        """The rehearsal's end, device time moved on between looks (never a host poll loop: each poll is a request)."""
        return self.sim.until(lambda: (lambda s: not s['phase'] and s)(self.progress()), "the rehearsal's end")

    def start(self, data, kind=1, deadline=120000):
        self.assertEqual(self.dev.stage(data), 0)
        code, state = R.call(self.dev, 0, struct.pack('<II', kind, deadline) + hashlib.sha256(data).digest())
        self.assertEqual(code, 0)
        return state

    def raw(self, body, cls=T.WRITE_CLASS, op=0x2B):
        return self.sim.ask_a(cls, op, HANDLE, argument2=len(body), body=body)

    def begin(self, data, token=100, kind=1, **kw):
        return self.raw(
            envelope(
                0, token, count=len(data), crc=zlib.crc32(data), kind=kind, data=hashlib.sha256(data).digest(), **kw
            )
        )

    @pytest.mark.sim
    def test_transport_loss_stops_without_retry_fallback_or_restart(self):
        """Kept: Install fault: a lost transport stops with no retry, fallback or restart."""
        self.ready()
        before = self.sim.status()
        accepted = self.start(self.stock)  # exactly one start, with A healthy and admission allowed
        self.assertGreater(accepted['phase'], 0)
        self.sim.set('a_slow_ms', 1000)
        for _ in range(400):  # 50 ms of device time between looks: inside A's 1 s answers
            progress = self.progress()
            if 0 < progress['done'] < progress['total'] and self.sim.status()['outstanding']:
                break  # A acknowledged bytes; the accepted transfer now has a class request out
            self.sim.advance(50)
        else:
            self.fail('no class request out mid transfer: %s' % progress)
        self.sim.set('a_silent', 1)
        self.sim.advance(45001)  # honor normal polls/answers through the existing 45 s request bound
        self.assertEqual(self.wait_done()['result'], P.S_LINK)
        later = self.sim.status()
        for key in (
            'a_boots',
            'b_boots',
            'boot_writes',
            'mount_calls',
            'storage_ops',
            'rw_mounts',
            'raw_boot_changes',
            'stage_executor_hits',
        ):
            self.assertEqual(later[key], before[key], key)
        self.assertEqual(self.dev.call(P.STATUS)[0], 0)
        self.assertEqual(self.dev.hello()[0], 0)
        self.sim.advance(1000)
        self.assertEqual(self.sim.status()['b_sent_ms'], later['b_sent_ms'])  # no post-loss A request


if __name__ == '__main__':
    unittest.main()
