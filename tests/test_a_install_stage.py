"""Phase-1 A staging: production transport, independent RAM, admission and no-write evidence."""
import hashlib
from pathlib import Path
import struct
import sys
import time
import unittest
import zlib

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
import test_simulator as T
from xy_forever import a_rehearsal as R, paths, sim, usb_proto_ids as P

HANDLE, MAGIC = 0x41535447, 0x31534941


def envelope(op, token=100, offset=0, count=0, crc=0, kind=0, data=b'', version=1):
    return struct.pack('<8I', MAGIC, version, op, token, offset, count, crc, kind) + data


@pytest.mark.sim
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

    def counters(self):
        status = self.sim.status()
        return {k: status[k] for k in ('mount_calls', 'rw_mounts', 'storage_ops', 'boot_writes', 'stage_executor_hits', 'raw_boot_changes')}

    def wait_done(self, timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            code, state = R.call(self.dev, 1)
            self.assertEqual(code, 0)
            if not state['phase']:
                return state
        self.fail('cooperative rehearsal did not finish')

    def start(self, data, kind=1, deadline=120000):
        self.assertEqual(self.dev.stage(data), 0)
        code, state = R.call(self.dev, 0, struct.pack('<II', kind, deadline) + hashlib.sha256(data).digest())
        self.assertEqual(code, 0)
        return state

    def raw_ready(self):
        self.ready()
        cmd = T.COMMAND_TAG << 16 | 3 << 8 | 8
        self.assertEqual(self.sim.ask_a(T.PARTITION_CLASS, 0x18, cmd), (~cmd, 0))
        self.assertEqual(self.sim.ask_a(T.PARTITION_CLASS, 9), (1, 0))

    def raw(self, body, cls=T.WRITE_CLASS, op=0x2b):
        return self.sim.ask_a(cls, op, HANDLE, argument2=len(body), body=body)

    def begin(self, data, token=100, kind=1, **kw):
        return self.raw(envelope(0, token, count=len(data), crc=zlib.crc32(data), kind=kind,
                                 data=hashlib.sha256(data).digest(), **kw))

    def test_real_stock_and_verified_product_transfer_admit_and_release_without_writes(self):
        self.ready()
        build = paths.newest_verified_build()
        self.assertIsNotNone(build, 'this source requires its own verified product build')
        product = (build / 'candidate.ldr').read_bytes()
        before = self.counters()
        for data, uninstall in ((self.stock, True), (product, False)):
            code, state = R.run(self.dev, data, uninstall)
            self.assertEqual(code, 0, state)
            self.assertEqual(state['done'], len(data))
            self.assertEqual(state['sha256'], hashlib.sha256(data).hexdigest())
            self.assertEqual(self.counters(), before)
        # A's success lease is gone: ordinary file operations and the original B writer still work.
        self.assertEqual(self.dev.read_file(T.BASE + 'state-a.txt')[0], 0)
        self.assertEqual(self.dev.stage(self.stock), 0)
        self.assertEqual(self.dev.uninstall(hashlib.sha256(self.stock).digest())[0], 0)
        self.assertGreater(self.sim.status()['raw_boot_changes'], 0)
        self.assertEqual(self.sim.status()['partition'], 0)
        self.assertEqual(self.dev.stage(product), 0)
        self.assertEqual(self.dev.install(hashlib.sha256(product).digest())[0], 0)
        self.assertEqual(self.sim.status()['partition'], 0)

    def test_device_qualification_sequence_and_menu_cleanup(self):
        """The documented sequence uses existing APIs; exact A mutation counters stay simulator-only."""
        self.ready()
        build = paths.newest_verified_build()
        self.assertIsNotNone(build)
        product = (build / 'candidate.ldr').read_bytes()
        # Separately qualify the shipping B installer/reboot before measuring RAM-only rehearsal.
        self.assertEqual(self.dev.stage(product), 0)
        self.assertEqual(self.dev.install(hashlib.sha256(product).digest())[0], 0)
        boots = self.sim.status()['b_boots']
        self.assertEqual(self.dev.call(P.REBOOT, struct.pack('<I', 2))[0], 0)
        self.dev.t.close()
        self.restarted(boots)
        self.dev = self.connect(startup_ready=True)  # bounded storage.space readiness, not HELLO alone
        self.assertEqual(self.dev.hello_info.proto, P.VERSION)
        self.assertTrue(self.dev.hello_info.answers(P.A_REHEARSAL))
        self.assertEqual(self.dev.hello_info.keys['image'], build.name)
        names = ('state-a.txt', 'state-b.txt', 'config.txt')
        before_files = {n: self.dev.read_file(T.BASE + n) for n in names}
        self.assertTrue(all(code in (P.S_OK, P.S_NOT_FOUND) for code, _ in before_files.values()))
        before = self.counters()
        before_boots = self.sim.status()['a_boots'], self.sim.status()['b_boots']
        for data, uninstall in ((product, False), (self.stock, True)):
            code, proof = R.run(self.dev, data, uninstall)
            self.assertEqual(code, 0, proof)
            self.assertEqual(proof['sha256'], hashlib.sha256(data).hexdigest())
            self.assertEqual((proof['phase'], proof['done'], proof['total']), (0, len(data), len(data)))
            self.assertEqual(self.counters(), before)
        self.start(self.stock)
        self.assertEqual(R.call(self.dev, 2)[0], 0)
        self.assertEqual(self.wait_done()['result'], P.S_CANCELLED)
        # Existing zero-size stage.begin forgets B's remaining input token; A is already released.
        self.assertEqual(self.dev.stage(b''), P.S_TOO_LARGE)
        self.assertEqual(self.dev.call(P.PUT_END)[0], P.S_STATE)
        self.assertEqual({n: self.dev.read_file(T.BASE + n) for n in names}, before_files)
        self.assertEqual(self.counters(), before)
        self.assertEqual(self.dev.call(P.HOLD, struct.pack('<I', 0))[0], 0)
        self.assertEqual(self.dev.call(P.STATUS)[0], 0)
        after = self.sim.status()
        self.assertEqual((after['a_boots'], after['b_boots']), before_boots)
        self.assertTrue(after['listening'])
        self.assertEqual((after['a'], after['b']), ('running', 'running'))

    def test_a_only_corruption_proves_local_hashing_and_ram_isolation(self):
        self.ready()
        before = self.counters()
        self.sim.set('a_stage_corrupt', 1)
        self.start(self.stock)
        self.assertEqual(self.wait_done()['result'], P.S_CRC)
        self.assertEqual(self.counters(), before)
        self.assertIn('corrupted A staging', self.sim.log.read_text())
        # The exact same B input is intact; it can be transferred again without restaging.
        code, _ = R.call(self.dev, 0, struct.pack('<II', 1, 120000) + hashlib.sha256(self.stock).digest())
        self.assertEqual(code, 0)
        self.assertEqual(self.wait_done()['result'], 0)
        self.assertEqual(self.counters(), before)

    def test_central_quarantine_inactive_malformed_and_competing_routes(self):
        self.raw_ready()
        before = self.counters()
        for cls, op in ((T.WRITE_CLASS, 0x2b), (T.WRITE_CLASS, 0x15), (T.WRITE_CLASS, 0x16),
                        (T.PARTITION_CLASS, 0x2d), (T.READ_CLASS, 0x2a), (T.PATH_CLASS, 0x29),
                        (0x11907388, 0x22), (T.SECTOR_CLASS, 0x2f)):
            reply = self.raw(b'x', cls, op)
            self.assertIsNotNone(reply, (hex(cls), op))
            self.assertNotEqual(reply, (0, 0))
        self.assertEqual(self.counters(), before)
        self.assertEqual(self.begin(self.stock), (0, 0))
        # A refuses this mount request while revoking the owner; it must not run the mount.
        self.assertEqual(self.sim.ask_a(T.PARTITION_CLASS, 8), (-1, T.EBUSY))
        self.assertEqual(self.raw(envelope(2)), (P.S_STATE, 0))
        self.assertEqual(self.begin(self.stock), (P.S_STATE, 0))  # replay remains burned
        self.assertEqual(self.counters(), before)

    def test_stock_write_count_must_match_body_and_malformed_frames_revoke_owner(self):
        self.raw_ready()
        before = self.counters()
        body = envelope(5)
        # Actual stock write layout: queue +0xc=0, count +0x18=32, body +0x24=32.
        self.assertEqual(self.raw(body), (0, 1))
        for token, count in enumerate((0, len(body) - 1, len(body) + 1), 100):
            self.assertEqual(self.begin(self.stock, token), (0, 0))
            self.assertEqual(self.sim.ask_a(T.WRITE_CLASS, 0x2b, HANDLE,
                                          argument2=count, body=body), (P.S_BAD_REQUEST, 0))
            self.assertEqual(self.raw(envelope(2, token)), (P.S_STATE, 0))
        self.assertEqual(self.counters(), before)

    def test_crc_order_replay_truncation_oversize_version_digest_and_kind_refuse(self):
        self.raw_ready()
        before = self.counters()
        token = 100
        for body, expected in (
            (envelope(0, count=0x700001, data=bytes(32)), P.S_TOO_LARGE),
            (envelope(0, token=101, count=32, kind=2, data=bytes(32)), P.S_BAD_REQUEST),
            (envelope(0, token=102, count=32, data=bytes(32), version=2), P.S_UNSUPPORTED),
        ):
            self.assertEqual(self.raw(body)[0], expected)
            token += 1
        # Each bad chunk revokes the lease, including duplicate offset (no retry/replay).
        for offset, payload, count, crc, expected in ((1, b'ab', 2, zlib.crc32(b'ab'), P.S_RANGE),
                (0, b'ab', 3, 0, P.S_RANGE), (0, b'ab', 2, 0, P.S_CRC)):
            token += 1
            self.assertEqual(self.begin(self.stock, token), (0, 0))
            self.assertEqual(self.raw(envelope(1, token, offset, count, crc, data=payload))[0], expected)
            self.assertEqual(self.raw(envelope(2, token))[0], P.S_STATE)
        token += 1
        self.assertEqual(self.begin(self.stock, token), (0, 0))
        piece = self.stock[:16]
        chunk = envelope(1, token, count=16, crc=zlib.crc32(piece), data=piece)
        self.assertEqual(self.raw(chunk), (0, 16))
        self.assertEqual(self.raw(chunk)[0], P.S_RANGE)
        token += 1
        self.assertEqual(self.begin(self.stock, token), (0, 0))
        self.assertEqual(self.raw(envelope(2, token))[0], P.S_STATE)  # incomplete input
        self.assertEqual(self.counters(), before)
    def test_a_checks_kind_sha_and_ldr_independently_of_staging_transport(self):
        self.ready()
        before = self.counters()
        # Host-staged bytes pass B's transport checks but A independently rejects the wrong kind/hash.
        self.start(self.stock, kind=0)
        self.assertEqual(self.wait_done()['result'], P.S_IMAGE)
        self.assertEqual(self.counters(), before)
        # A full CRC matches, but A's own digest differs from the declared value.
        tiny = b'x' * 32
        self.assertEqual(self.raw(envelope(0, count=32, crc=zlib.crc32(tiny), data=bytes(32))), (0, 0))
        self.assertEqual(self.raw(envelope(1, count=32, crc=zlib.crc32(tiny), data=tiny)), (0, 32))
        self.assertEqual(self.raw(envelope(2)), (P.S_SHA, 0))
        # Descriptor acceptance alone is insufficient: this is not a structurally valid LDR.
        invalid = struct.pack('<II', 0x584c4348, 1).ljust(32, b'x')
        self.assertEqual(self.begin(invalid, token=101, kind=0), (0, 0))
        self.assertEqual(self.raw(envelope(1, 101, count=32, crc=zlib.crc32(invalid), data=invalid)), (0, 32))
        self.assertEqual(self.raw(envelope(2, 101)), (P.S_IMAGE, 0))
        self.assertEqual(self.counters(), before)

    def test_cancel_deadline_and_usb_status_while_a_is_delayed(self):
        self.ready()
        self.sim.set('a_slow_ms', 1000)
        before = self.counters()
        self.start(self.stock)
        start = time.monotonic()
        self.assertEqual(self.dev.call(P.STATUS)[0], 0)
        self.assertLess(time.monotonic() - start, .75)
        self.assertEqual(R.call(self.dev, 2)[0], 0)
        self.sim.set('a_slow_ms', 0)
        self.assertEqual(self.wait_done()['result'], P.S_CANCELLED)
        self.assertEqual(self.counters(), before)
        self.start(self.stock, deadline=1)
        self.assertEqual(self.wait_done()['result'], P.S_CANCELLED)
        self.assertEqual(self.counters(), before)
        self.start(self.stock)
        self.assertEqual(self.wait_done()['result'], 0)

    def test_usb_version_state_token_and_competing_commands(self):
        self.ready()
        extra = struct.pack('<II', 1, 120000) + hashlib.sha256(self.stock).digest()
        self.assertEqual(self.dev.call(P.A_REHEARSAL, struct.pack('<II', 2, 1))[0], P.S_UNSUPPORTED)
        self.assertEqual(R.call(self.dev, 0, extra)[0], P.S_STATE)
        self.assertEqual(self.dev.stage(self.stock), 0)
        self.assertEqual(R.call(self.dev, 0, extra[:-32] + bytes(32))[0], P.S_SHA)
        self.assertEqual(R.call(self.dev, 0, struct.pack('<II', 2, 120000) + extra[-32:])[0], P.S_BAD_REQUEST)
        self.sim.set('a_slow_ms', 1000)
        self.assertEqual(R.call(self.dev, 0, extra)[0], 0)
        self.assertEqual(R.call(self.dev, 0, extra)[0], P.S_BUSY)
        for command, payload in ((P.CONTINUE, b''), (P.REBOOT, b''), (P.INSTALL, extra[-32:]),
                                  (P.UNINSTALL, extra[-32:]), (P.READ_FILE, (T.BASE + 'state-a.txt').encode() + b'\0')):
            self.assertEqual(self.dev.call(command, payload)[0], P.S_BUSY)
        self.assertEqual(R.call(self.dev, 2)[0], 0)
        self.sim.set('a_slow_ms', 0)
        self.assertEqual(self.wait_done()['result'], P.S_CANCELLED)
        self.assertEqual(self.dev.stage(b'next input'), 0)
        self.assertEqual(R.call(self.dev, 1)[1]['crc32'], zlib.crc32(self.stock))

    def test_a_idle_expiry_abort_restart_and_competing_boot_do_not_admit_stale_bytes(self):
        self.raw_ready()
        before = self.counters()
        self.assertEqual(self.begin(self.stock), (0, 0))
        self.sim.advance(10001)
        self.assertEqual(self.raw(envelope(2)), (P.S_STATE, 0))
        self.assertEqual(self.begin(self.stock, token=101), (0, 0))
        self.assertEqual(self.raw(envelope(4, token=101)), (0, 0))
        self.assertEqual(self.begin(self.stock, token=102), (0, 0))
        # CM transport ACK is receipt; the active lease makes A discard this explicit BOOT action.
        command = T.COMMAND_TAG << 16 | 0x11 << 8 | 16
        self.assertEqual(self.sim.ask_a(T.PARTITION_CLASS, 0x18, command), (~command, 0))
        self.assertEqual(self.raw(envelope(2, token=102)), (P.S_STATE, 0))
        self.assertEqual(self.dev.call(P.STATUS)[0], 0)
        self.assertEqual(self.counters(), before)

    def test_transport_loss_stops_without_retry_fallback_or_restart(self):
        self.ready()
        before = self.sim.status()
        accepted = self.start(self.stock)  # exactly one start, with A healthy and admission allowed
        self.assertGreater(accepted['phase'], 0)
        self.sim.set('a_slow_ms', 1000)
        deadline = time.monotonic() + 10
        while True:
            code, progress = R.call(self.dev, 1)
            self.assertEqual(code, 0)
            if 0 < progress['done'] < progress['total'] and self.sim.status()['outstanding']:
                break  # A acknowledged bytes; the accepted transfer now has a class request out
            self.assertLess(time.monotonic(), deadline, progress)
        self.sim.set('a_silent', 1)
        self.sim.advance(45001)  # honor normal polls/answers through the existing 45 s request bound
        self.assertEqual(self.wait_done()['result'], P.S_LINK)
        later = self.sim.status()
        for key in ('a_boots', 'b_boots', 'boot_writes', 'mount_calls', 'storage_ops',
                    'rw_mounts', 'raw_boot_changes', 'stage_executor_hits'):
            self.assertEqual(later[key], before[key], key)
        self.assertEqual(self.dev.call(P.STATUS)[0], 0)
        self.assertEqual(self.dev.hello()[0], 0)
        self.sim.advance(1000)
        self.assertEqual(self.sim.status()['b_sent_ms'], later['b_sent_ms'])  # no post-loss A request

    def test_accepted_inflight_request_drop_ends_without_retry_or_mutation(self):
        self.ready()
        before = self.sim.status()
        self.assertGreater(self.start(self.stock)['phase'], 0)
        self.sim.set('drop_request', 1)  # existing hook: next class request of this accepted job
        self.wait(lambda s: s['drop_request'] == 0,
                  what='accepted job request dropped in flight')
        self.sim.advance(45001)
        self.assertEqual(self.wait_done()['result'], P.S_LINK)
        later = self.sim.status()
        for key in ('a_boots', 'b_boots', 'boot_writes', 'mount_calls', 'storage_ops',
                    'rw_mounts', 'raw_boot_changes', 'stage_executor_hits'):
            self.assertEqual(later[key], before[key], key)
        self.assertEqual(self.dev.call(P.STATUS)[0], 0)
        self.assertEqual(self.dev.hello()[0], 0)
        self.sim.advance(1000)
        self.assertEqual(self.sim.status()['b_sent_ms'], later['b_sent_ms'])

    def test_preflight_refuses_unexpected_a_write_window_without_checkpointing_it(self):
        self.ready()
        self.assertEqual(self.dev.stage(self.stock), 0)  # B is unaware of the raw fixture's A window
        command = T.COMMAND_TAG << 16 | T.FILE_WRITE_ITEM << 8 | 16
        self.assertEqual(self.sim.ask_a(T.PARTITION_CLASS, 0x18, command), (~command, 0))
        self.assertEqual(self.sim.ask_a(T.PARTITION_CLASS, 8), (0, 0))
        self.assertEqual(self.sim.ask_a(T.PATH_CLASS, 0x26, 0x1ff,
                                       body=T.raw_path(T.BASE + 'preflight-fixture')), (0, 0))
        self.assertEqual(self.sim.status()['rw_open'], 1)
        before = self.counters()
        code, _ = R.call(self.dev, 0, struct.pack('<II', 1, 120000) + hashlib.sha256(self.stock).digest())
        self.assertEqual(code, 0)
        self.assertEqual(self.wait_done()['result'], P.S_BUSY)
        self.assertEqual(self.counters(), before)  # especially no op9 checkpoint or mount
        self.assertEqual(self.sim.status()['rw_open'], 1)
        self.assertEqual(self.dev.call(P.STATUS)[0], 0)


if __name__ == '__main__':
    unittest.main()
