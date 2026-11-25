"""XY Forever USB service: frame codec (device C++ vs host Python), the exact
install allowlist, image/header checks, decoder layout, builder spans, and the
host tool against a fake device. No device access."""

import contextlib
import hashlib
import io
import json
import random
import struct
import tempfile
import time
import unittest
import zlib
from pathlib import Path

import pytest
import xyf_hosttest
from conftest import verified_build

from xy_forever import usb_proto as P
from xy_forever.census_usb import (
    USB_SERVICE_FIELDS,
    USB_SERVICE_V2_WORDS,
    USB_SERVICE_V3_WORDS,
    USB_SERVICE_V4_WORDS,
    usb_service,
)
from xy_forever.paths import STOCK_121

ROOT = Path(__file__).resolve().parents[1]

PARTITION, SECTOR, WRITE, PATH, READ = 0x11907694, 0x11907348, 0x11907710, 0x119076B4, 0x11907368
(NONE, SELECT, TRIM, RD, WR, REFUSE_PARTITION, REFUSE_SELECTED, REFUSE_OP, REFUSE_FILES) = range(9)


class UsbServiceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lib = xyf_hosttest.module('usb_service')

    def device_encode(self, kind, seq, payload):
        return self.lib.encode(kind, seq, payload, P.MAX_PAYLOAD + 16)

    def device_feed(self, data):
        return self.lib.feed(data)

    def test_codec_matches_between_device_and_host(self):
        rng = random.Random(1)
        for size in (0, 1, 31, 4096, P.MAX_PAYLOAD):
            payload = bytes(rng.getrandbits(8) for _ in range(size))
            frame = P.encode(0x11, 7, payload)
            self.assertEqual(self.device_encode(0x11, 7, payload), frame)
            self.lib.reset()
            self.assertEqual(self.device_feed(b'noise' + frame)[:4], (1, 0x11, 7, payload))
            self.assertEqual(P.Parser().feed(b'noise' + frame), [(0x11, 7, payload)])
        self.assertEqual(self.lib.encode(1, 1, b'x' * (P.MAX_PAYLOAD + 1), 0), b'')

    def test_corruption_is_rejected_and_resynchronised(self):
        frame = P.encode(0x02, 3, b'hello')
        for i in range(len(frame)):
            bad = bytearray(frame)
            bad[i] ^= 0x10
            self.lib.reset()
            frames, kind, seq, payload, bad_crc, _ = self.device_feed(bytes(bad) + frame)
            host = P.Parser().feed(bytes(bad) + frame)
            if frames:  # the good frame after it decodes...
                self.assertEqual((kind, seq, payload), (0x02, 3, b'hello'))
                self.assertEqual(host[-1], (0x02, 3, b'hello'))
            else:  # ...or a corrupted length still waits (home_pump's idle reset clears it)
                self.assertTrue(4 <= i < 8, i)
                self.lib.reset()
                self.assertEqual(self.device_feed(frame)[:4], (1, 0x02, 3, b'hello'))
            self.assertNotIn((0x02, 3, b'hellp'), host)  # never a corrupted frame accepted
        self.lib.reset()
        self.assertEqual(self.device_feed(b'XF\x01\x01' + struct.pack('<I', P.MAX_PAYLOAD + 1) + frame)[0], 1)

    def test_random_streams_decode_identically(self):
        rng = random.Random(9)
        for _ in range(60):
            stream, sent = bytearray(), []
            for k in range(rng.randrange(1, 6)):
                if rng.random() < 0.5:
                    stream += bytes(rng.getrandbits(8) for _ in range(rng.randrange(0, 40)))
                payload = bytes(rng.getrandbits(8) for _ in range(rng.randrange(0, 300)))
                frame = bytearray(P.encode(0x11, k, payload))
                if rng.random() < 0.2:
                    frame[rng.randrange(len(frame))] ^= 1 << rng.randrange(8)
                else:
                    sent.append((0x11, k, payload))
                stream += frame
            host = P.Parser().feed(bytes(stream))
            self.lib.reset()
            device = []
            for byte in stream:
                frames, kind, seq, payload, _, _ = self.device_feed(bytes([byte]))
                if frames:
                    device.append((kind, seq, payload))
            self.assertEqual(device, host)
            for frame in host:
                if frame[1] < 0x80 and frame[2] and frame not in sent:
                    self.assertFalse(any(frame[1] == s[1] for s in sent), frame[:2])  # no altered frame accepted

    def test_install_allowlist_is_exactly_track5(self):
        d = self.lib.install_decide
        for p in (0, 1, 2):
            self.assertEqual(d(PARTITION, 0x18, p, 0), SELECT)
        self.assertEqual(d(PARTITION, 0x18, 3, 0), REFUSE_PARTITION)
        self.assertEqual(d(PARTITION, 0x14, 0, 0), REFUSE_SELECTED)  # never trim the user partition
        self.assertEqual(d(PARTITION, 0x14, 0, 1), TRIM)
        self.assertEqual(d(WRITE, 0x16, 0, 0), REFUSE_SELECTED)  # never write the user partition
        self.assertEqual(d(WRITE, 0x15, 0, 2), WR)
        self.assertEqual(d(WRITE, 0x16, 0, 1), WR)
        self.assertEqual(d(WRITE, 0x17, 0, 1), REFUSE_OP)
        self.assertEqual(d(SECTOR, 0x11, 0, 0), RD)
        self.assertEqual(d(SECTOR, 0x12, 0, 2), RD)
        self.assertEqual(d(SECTOR, 0x13, 0, 2), REFUSE_OP)
        for vt, op in ((PARTITION, 8), (PARTITION, 9), (PARTITION, 0x2C), (PATH, 0x29), (READ, 0x2A)):
            self.assertEqual(d(vt, op, 0, 0), NONE)  # read-only allowlist decides
            self.assertEqual(d(vt, op, 0, 1), REFUSE_FILES)  # no file I/O on a boot partition
        for op in (0x04, 0x30, 0x34, 0x36):
            self.assertEqual(d(PARTITION, op, 0, 0), NONE)  # -> read-only allowlist refuses them

    def test_image_and_header_checks(self):
        image = bytes(64) + struct.pack('<II', 0x584C4348, 1) + bytes(64)
        self.assertTrue(self.lib.has_descriptor(image))
        self.assertFalse(self.lib.has_descriptor(bytes(200)))
        self.assertFalse(self.lib.has_descriptor(image[:66] + image[67:]))  # misaligned
        header = struct.pack('<IIIBB', 3, 1000, 0xDEADBEEF, 1, 0) + bytes(498)
        self.assertTrue(self.lib.header_matches(header, 1000, 0xDEADBEEF))
        self.assertFalse(self.lib.header_matches(header, 1001, 0xDEADBEEF))
        self.assertFalse(self.lib.header_matches(header, 1000, 0xDEADBEEE))
        encrypted = struct.pack('<IIIBB', 3, 1000, 0xDEADBEEF, 1, 1) + bytes(498)  # OTP key: writer encrypts
        self.assertTrue(self.lib.header_matches(encrypted, 1000, 0xDEADBEEF))
        for bad in (
            struct.pack('<IIIBB', 1, 1000, 0xDEADBEEF, 1, 0),
            struct.pack('<IIIBB', 3, 1000, 0xDEADBEEF, 0, 0),
            struct.pack('<IIIBB', 3, 1000, 0xDEADBEEF, 1, 2),
        ):
            self.assertFalse(self.lib.header_matches(bad + bytes(498), 1000, 0xDEADBEEF))

    def test_uninstall_accepts_only_the_exact_stock_image(self):
        stock = bytes.fromhex(P.STOCK_OS_LDR_SHA256)
        self.assertEqual(self.lib.stock_sha(), stock)  # device == host constants
        self.assertEqual(self.lib.stock_bytes(), P.STOCK_OS_LDR_BYTES)
        size = P.STOCK_OS_LDR_BYTES
        accept = lambda u, d, n, sha: self.lib.accept_image(u, d, n, sha)
        self.assertTrue(accept(1, 0, size, stock))  # stock: no descriptor needed
        self.assertFalse(accept(1, 0, size - 1, stock))  # size must match too
        self.assertFalse(accept(1, 1, size, bytes(32)))  # any other image, descriptor or not
        for i in range(32):
            other = bytearray(stock)
            other[i] ^= 1
            self.assertFalse(accept(1, 0, size, bytes(other)))
        self.assertFalse(accept(1, 0, size, None))
        # INSTALL unchanged: the XY Forever descriptor decides, never the stock SHA.
        self.assertTrue(accept(0, 1, 12345, bytes(32)))
        self.assertFalse(accept(0, 0, size, stock))  # stock image via INSTALL: refused
        self.assertFalse(accept(0, 0, 12345, bytes(32)))

    def test_stock_restore_image_matches_constants(self):
        restore = STOCK_121 / 'os.ldr'
        if not restore.exists():
            self.skipTest('private stock 1.2.1 os.ldr absent')
        data = restore.read_bytes()
        self.assertEqual((len(data), hashlib.sha256(data).hexdigest()), (P.STOCK_OS_LDR_BYTES, P.STOCK_OS_LDR_SHA256))
        self.assertFalse(self.lib.has_descriptor(data))  # needs UNINSTALL, not INSTALL

    def test_result_layout_matches_decoder(self):
        self.assertEqual(self.lib.result_bytes(), 4 * USB_SERVICE_V4_WORDS)
        words = [0] * USB_SERVICE_V4_WORDS
        words[USB_SERVICE_V2_WORDS:USB_SERVICE_V3_WORDS] = [57, 56, 1, 0, 0, 4, 81234, 900000]  # version 3: install log
        words[USB_SERVICE_V3_WORDS:] = [0x16, 0x28, 0, 0]  # version 4: presence
        words[0] = 0x31425355
        words[USB_SERVICE_FIELDS.index('exit')] = 8
        words[USB_SERVICE_FIELDS.index('install_status')] = 0
        base = len(USB_SERVICE_FIELDS)
        words[base : base + 4] = [2, 12, 2, 0]  # rx_events, rx_bytes, diag_count
        snap = base + 4 + 24  # second snapshot
        words[snap] = 3000
        words[snap + 5] = 0x0005_012A  # FRAME 0x12a, POWER 0x05
        words[snap + 8] = 0x000C_0001  # EP1 RXCSR RxPktRdy, RXCOUNT 12
        words[snap + 15] = 0x0008_0000  # DMA channel 3 CTL 0x8
        words[snap + 18] = 0x88AA0000
        words[snap + 19] = 1
        block = struct.pack('<%dI' % len(words), *words)
        out = usb_service([dict(address=0x8AB0A000, bytes=len(block), offset=0)], block)
        self.assertEqual((out['exit'], out['install_status'], out['rx_bytes']), ('reboot', 'ok', 12))
        self.assertEqual(len(out['diag']), 2)
        d = out['diag'][1]
        self.assertEqual(
            (d['ms'], d['frame'], d['power'], d['endpoints'][0]['rxcsr'], d['endpoints'][0]['rxcount']),
            (3000, 0x12A, '0x5', '0x1', 12),
        )
        self.assertEqual((d['dma_ctl'][3], d['cdc']['open'], d['cdc']['port']), ('0x8', 1, '0x88aa0000'))
        self.assertEqual(
            (out['log_lines'], out['log_sent'], out['install_step'], out['session_cap']), (57, 56, 'writer', 900000)
        )
        self.assertEqual((out['power_status'], out['boot_reason']), ('0x16', '0x28'))
        v3 = struct.pack('<%dI' % USB_SERVICE_V3_WORDS, *words[:USB_SERVICE_V3_WORDS])  # version 3 (Tests 035-038)
        out3 = usb_service([dict(address=0, bytes=len(v3), offset=0)], v3)
        self.assertEqual(out3['install_step'], 'writer')
        self.assertNotIn('power_status', out3)
        v2 = struct.pack('<%dI' % USB_SERVICE_V2_WORDS, *words[:USB_SERVICE_V2_WORDS])  # version 2 (Tests 032-034)
        out2 = usb_service([dict(address=0, bytes=len(v2), offset=0)], v2)
        self.assertEqual(len(out2['diag']), 2)
        self.assertNotIn('log_lines', out2)
        old = struct.pack('<%dI' % len(USB_SERVICE_FIELDS), *words[: len(USB_SERVICE_FIELDS)])  # version 1 (Test 031)
        self.assertNotIn('diag', usb_service([dict(address=0, bytes=len(old), offset=0)], old))
        # Version 5 (#107): the build identity after the stage-2 record (diag_count 0).
        words[1], words[base + 2] = 5, 0
        words[base + 12 : base + 17] = [0x000100, 0xF458E3CC, 2, 85, 30000]
        v5 = struct.pack('<%dI' % len(words), *words)
        out5 = usb_service([dict(address=0, bytes=len(v5), offset=0)], v5)
        self.assertEqual(out5['build'], dict(version='0.1.0', image='f458e3cc', state='known', ms=85, kcycles=30000))
        words[base + 14] = 3  # unknown
        v5 = struct.pack('<%dI' % len(words), *words)
        self.assertEqual(usb_service([dict(address=0, bytes=len(v5), offset=0)], v5)['build']['image'], 'unknown')
        self.assertNotIn('replies_lost', out5)
        # Version 6 (#314): replies that could not be written, after the identity.
        words[1] = 6
        words[base + 17 : base + 20] = [2, 41233, 1500]
        v6 = struct.pack('<%dI' % len(words), *words)
        out6 = usb_service([dict(address=0, bytes=len(v6), offset=0)], v6)
        self.assertEqual(out6['replies_lost'], dict(outages=2, out_since_ms=41232, longest_ms=1500))

    def test_stall_log_decodes_the_newest_entries(self):
        # #314: the stall recorder's block (target/key_ui.hpp StallLog): 16 slots, entry i in slot i % 16.
        from xy_forever.census import stall_log

        words = [0x314C5453, 1, 18, 0] + [0] * 80
        for i in (16, 17):  # slots 0 and 1 hold entries 16 and 17
            at = 4 + 5 * (i % 16)
            words[at : at + 5] = [1000 + i, 0x8AB03570, 0x8AB04000, 0x8BFFEF00, 0x8000 | (i - 15) << 16]
        out = stall_log(struct.pack('<84I', *words))
        self.assertEqual(out['count'], 18)
        self.assertEqual(len(out['entries']), 16)
        self.assertEqual(
            out['entries'][-1],
            dict(n=17, ms=1017, pc='8ab03570', rets='8ab04000', fp='8bffef00', ipend='0x8000', ticks=2),
        )
        self.assertIsNone(stall_log(b'\0' * 336))

    def test_spans_match_bootloader_and_cover_calls(self):
        boot = STOCK_121 / 'bootloader.ldr'
        if not boot.exists():
            self.skipTest('private bootloader absent')
        from xy_forever import xyf_build as B

        B.check_usb_spans(boot.read_bytes())
        for a, _n, _ in B.usb_spans():
            self.assertFalse(0x11A00000 <= a < 0x11A0C000)
        self.assertTrue(B.FORBIDDEN_TARGETS.isdisjoint(B.USB_CALLS))


class FakeDevice:
    """Python model of the device protocol (usb_service.cpp) for host-tool tests."""

    def __init__(self, files=None):
        self.out = bytearray()
        self.parser = P.Parser()
        self.files = files or {}
        self.staged = None
        self.expected = None
        self.installed = None
        self.greeted = False

    def write(self, data):
        for kind, seq, payload in self.parser.feed(data):
            self.out += P.encode(kind | P.REPLY, seq, self.handle(kind, payload))

    def read(self, timeout):
        data, self.out = bytes(self.out), bytearray()
        return data

    def handle(self, kind, payload):
        ok = struct.pack('<I', 0)
        if kind == P.HELLO:
            self.greeted = True
            return ok + b'XYF1;service=usb;role=b;proto=1'
        if not self.greeted:
            return struct.pack('<I', 3)
        if kind == P.READ_FILE:
            end = payload.index(b'\0')
            path = payload[:end].decode()
            if path not in self.files:
                return struct.pack('<I', 8)
            if len(payload) == end + 9:  # ranged (Test 039: offset, length <= 1 KiB)
                offset, length = struct.unpack_from('<II', payload, end + 1)
                if not 0 < length <= 1024:
                    return struct.pack('<I', 1)
                return ok + self.files[path][offset : offset + length]
            data = self.files[path]
            return ok + data if len(data) <= P.MAX_DATA else struct.pack('<I', 9)
        if kind == P.ids.WRITE_BEGIN:  # 1.18: streamed; the end acts as FILE_WRITE
            end = payload.index(b'\0')
            size, crc = struct.unpack_from('<II', payload, end + 1)
            self.stream = (payload[:end], (size, crc, payload[end + 9 : end + 41]), bytearray())
            return ok
        if kind == P.ids.WRITE_DATA:
            offset = struct.unpack_from('<I', payload)[0]
            if offset != len(self.stream[2]):
                return struct.pack('<I', 4)
            self.stream[2].extend(payload[4:])
            return ok + struct.pack('<I', len(self.stream[2]))
        if kind == P.ids.WRITE_END:
            path, self.expected, self.staged = self.stream
            return self.handle(P.FILE_WRITE, path + b'\0' + self.expected[2]) + struct.pack('<I', 0)
        if kind == P.PUT_BEGIN:
            size, crc = struct.unpack_from('<II', payload)
            self.expected = (size, crc, payload[8:40])
            self.staged = bytearray()
            return ok
        if kind == P.PUT_DATA:
            offset = struct.unpack_from('<I', payload)[0]
            if offset != len(self.staged):
                return struct.pack('<I', 4)
            self.staged += payload[4:]
            return ok + struct.pack('<I', len(self.staged))
        if kind == P.PUT_END:
            size, crc, sha = self.expected
            good = (
                len(self.staged) == size
                and zlib.crc32(self.staged) == crc
                and hashlib.sha256(self.staged).digest() == sha
            )
            return struct.pack('<I', 0 if good else 6)
        if kind == P.UNINSTALL:
            if payload != self.expected[2]:
                return struct.pack('<IIII', 6, 0, 0, 0)
            if (
                len(self.staged) != P.STOCK_OS_LDR_BYTES
                or hashlib.sha256(self.staged).hexdigest() != P.STOCK_OS_LDR_SHA256
            ):
                return struct.pack('<IIII', 10, 0, 0, 0)
            self.installed = bytes(self.staged)
            return struct.pack('<IIII', 0, len(self.staged), 1, (len(self.staged) >> 9) + 1)
        if kind == P.INSTALL:
            if payload != self.expected[2]:
                return struct.pack('<IIII', 6, 0, 0, 0)
            self.installed = bytes(self.staged)
            return struct.pack('<IIII', 0, len(self.staged), 1, (len(self.staged) >> 9) + 1)
        return ok


class ReadThenContinueTest(unittest.TestCase):
    """Test 046: a READ_FILE left a's storage unable to mount fresh in the v2 service, which
    refused CONTINUE (state); the host REBOOTs, reconnects and continues (still supported)."""

    def test_host_reboots_then_continues(self):
        from xy_forever import xyf_host as host

        class Booting(FakeDevice):
            def __init__(self, log, read_done):
                super().__init__()
                self.calls = log
                self.read_done = read_done

            def close(self):
                self.calls.append('close')

            def handle(self, kind, payload):
                if kind != P.HELLO:
                    self.calls.append(kind)
                if kind == P.CONTINUE and self.read_done:
                    return struct.pack('<I', P.STATE)
                return super().handle(kind, payload)

        calls, devices = [], []

        def connect(timeout):
            fake = Booting(calls, read_done=not devices)  # first boot had a read
            devices.append(fake)
            dev = host.Device(fake)
            dev.hello()
            return dev, 'fake%d' % len(devices), 'XYF1'

        original, sleep = host.connect, host.time.sleep
        host.connect, host.time.sleep = connect, lambda s: None
        try:
            self.assertEqual(host.main(['continue']), 0)
            self.assertEqual(calls, [P.CONTINUE, P.REBOOT, 'close', P.CONTINUE])
            calls.clear()
            devices.clear()
            self.assertEqual(host.main(['continue', '--dry-run']), 0)
            self.assertEqual(calls, [P.CONTINUE, P.REBOOT, 'close', P.CONTINUE])
        finally:
            host.connect, host.time.sleep = original, sleep


@pytest.mark.gate
class HostToolTest(unittest.TestCase):
    def setUp(self):
        from xy_forever import xyf_host

        self.host = xyf_host

    def build_dir(self, tmp, data, passed=True):
        d = Path(tmp)
        (d / 'candidate.ldr').write_bytes(data)
        (d / 'manifest.json').write_text(
            json.dumps(dict(mode='xyf-usb', candidate_sha256=hashlib.sha256(data).hexdigest()))
        )
        (d / 'verification.json').write_text(json.dumps(dict(passed=passed)))
        return d

    def test_real_build_validates_and_installs_on_fake_device(self):
        build = verified_build(self)
        data, notes = self.host.load_build(build)
        self.assertTrue(any('verified' in n for n in notes))
        fake = FakeDevice({'/yaffs2/firmware.version': b'1.2.1+0@abc\n'})
        dev = self.host.Device(fake)
        self.assertEqual(dev.hello()[0], 0)
        self.assertEqual(dev.read_file('/yaffs2/firmware.version'), (0, b'1.2.1+0@abc\n'))
        self.assertEqual(dev.read_file('/yaffs2/missing')[0], 8)
        self.assertEqual(dev.stage(data), 0)
        self.assertEqual(dev.install(b'\0' * 32)[0], 6)  # wrong confirm token: refused
        self.assertIsNone(fake.installed)
        code, written, partition, sectors = dev.install(hashlib.sha256(data).digest())
        self.assertEqual((code, written, partition), (0, len(data), 1))
        self.assertEqual(fake.installed, data)

    def test_connect_repeats_hello_and_closes_silent_ports(self):
        class Deaf(FakeDevice):  # Test 031: bytes written, nothing ever answered
            closed = 0

            def write(self, data):
                pass

            def close(self):
                Deaf.closed += 1

        class Late(FakeDevice):  # answers from the third HELLO on
            hellos = 0

            def write(self, data):
                Late.hellos += 1
                if Late.hellos >= 3:
                    FakeDevice.write(self, data)

            def close(self):
                pass

        host = self.host
        client = host.device_client
        saved = client.find_port, client.SerialPort, client.log
        try:
            client.log = lambda message: None
            client.find_port = lambda: '/dev/cu.usbmodemTEST1'
            client.SerialPort = lambda path: Late()
            dev, port, text = host.connect(5.0, hello_every=0.01, hello_window=2.0)
            self.assertEqual((port, Late.hellos), ('/dev/cu.usbmodemTEST1', 3))
            self.assertTrue(text.startswith('XYF1'))
            client.SerialPort = lambda path: Deaf()
            start = time.monotonic()
            with self.assertRaises(SystemExit):
                host.connect(0.5, hello_every=0.01, hello_window=0.2)
            self.assertLess(time.monotonic() - start, 2.0)  # hard deadline
            self.assertGreaterEqual(Deaf.closed, 1)  # every silent port closed
        finally:
            client.find_port, client.SerialPort, client.log = saved

    def test_refuses_anything_not_verified(self):
        with tempfile.TemporaryDirectory() as tmp:
            good = bytes(0x10000) + struct.pack('<II', 0x584C4348, 1)
            with self.assertRaises(ValueError):
                self.host.load_build(self.build_dir(tmp, good, passed=False))  # verification failed
            d = self.build_dir(tmp, good)
            (d / 'candidate.ldr').write_bytes(good + b'x')
            with self.assertRaises(ValueError):
                self.host.load_build(d)  # SHA mismatch
            plain = Path(tmp) / 'plain.ldr'
            plain.write_bytes(bytes(0x20000))
            with self.assertRaises(ValueError):
                self.host.load_build(plain)  # no descriptor
            plain.write_bytes(b'\x01')
            with self.assertRaises(ValueError):
                self.host.load_build(plain)  # too small
            plain.write_bytes(good)
            with self.assertRaises(ValueError):
                self.host.load_build(plain)  # bare file, even with a descriptor

    def test_uninstall_refuses_other_files_and_defaults_to_dry_run(self):
        called = []
        original = self.host.connect
        self.host.connect = lambda timeout: called.append(1)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                other = Path(tmp) / 'os.ldr'
                other.write_bytes(bytes(P.STOCK_OS_LDR_BYTES))
                self.assertEqual(self.host.main(['uninstall', str(other), '--yes-uninstall']), 2)  # wrong SHA
                self.assertEqual(self.host.main(['uninstall', tmp, '--yes-uninstall']), 2)  # not a file
                with self.assertRaises(ValueError):
                    self.host.load_stock(other)
            restore = STOCK_121 / 'os.ldr'
            if restore.exists():
                self.assertEqual(self.host.main(['uninstall', str(restore)]), 0)  # dry run
                with self.assertRaises(ValueError):
                    self.host.load_build(restore)  # never via install
        finally:
            self.host.connect = original
        self.assertEqual(called, [])

    def test_uninstall_on_fake_device(self):
        restore = STOCK_121 / 'os.ldr'
        if not restore.exists():
            self.skipTest('private stock 1.2.1 os.ldr absent')
        data, notes = self.host.load_stock(restore)
        fake = FakeDevice()
        dev = self.host.Device(fake)
        self.assertEqual(dev.hello()[0], 0)
        self.assertEqual(dev.stage(data), 0)
        self.assertEqual(dev.uninstall(b'\0' * 32)[0], 6)  # wrong token
        self.assertIsNone(fake.installed)
        self.assertEqual(dev.uninstall(hashlib.sha256(data).digest())[:3], (0, len(data), 1))
        self.assertEqual(fake.installed, data)

    def test_hello_gates_commands(self):
        """A v1 device's `cmds` decides what the host sends; a pre-v1 HELLO (build 153 and
        earlier: no cmds) and a missing proto gate nothing; another major version stops the tool."""
        import contextlib
        import io

        host = self.host

        class Device(FakeDevice):
            def __init__(self, text):
                super().__init__()
                self.text = text
                self.sent = []

            def handle(self, kind, payload):
                self.sent.append(kind)
                if kind == P.HELLO:
                    self.greeted = True
                    return struct.pack('<I', 0) + self.text.encode()
                return super().handle(kind, payload)

        def run(text, argv):
            fake = Device(text)

            def connect(timeout):
                dev = host.Device(fake)
                dev.hello()
                return dev, 'fake', text

            original, out = host.connect, io.StringIO()
            host.connect = connect
            try:
                with contextlib.redirect_stdout(out):
                    code = host.main(argv)
            finally:
                host.connect = original
            return code, out.getvalue(), fake.sent

        lean = P.hello_text('0.1.0', 'f458e3cc', 'product', set())  # no file write, set default, MTP test
        full = P.hello_text('0.1.0', 'f458e3cc', 'product', {'XYF_MTP', 'XYF_USB_FILE_WRITE', 'XYF_USB_SET_DEFAULT'})
        old = 'XYF1;service=usb;role=b;proto=1;install=stock-88a06a7e'
        code, out, sent = run(full, ['wait'])
        self.assertEqual(code, 0)
        self.assertIn('protocol %s, version 0.1.0, image f458e3cc (product)' % P.ids.VERSION, out)
        older = (
            'XYF1;service=usb;role=b;proto=1.13;install=stock-88a06a7e;build=154;mode=product;cmds='
            + full.split('cmds=')[1]
        )
        code, out, sent = run(older, ['wait'])  # an older 1.x: a build number
        self.assertEqual(code, 0)
        self.assertIn('protocol 1.13, build 154 (product)', out)
        code, out, sent = run(old, ['wait'])
        self.assertEqual(code, 0)
        self.assertIn('protocol 1: pre-v1 device', out)
        code, out, sent = run(lean, ['set-default', 'opxy-121', '--yes-write'])
        self.assertEqual(code, P.ids.S_UNSUPPORTED)
        self.assertIn('does not answer library.set_default (0x40); update XY Forever', out)
        self.assertEqual(sent, [P.HELLO])  # refused before sending
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / 'x.txt'
            local.write_bytes(b'hello')
            code, out, sent = run(lean, ['put-file', str(local), '/yaffs2/xy-forever/x.txt', '--yes-write'])
            self.assertEqual(code, P.ids.S_UNSUPPORTED)
            self.assertNotIn(P.FILE_WRITE, sent)
        code, out, sent = run('XYF1;proto=2.0;cmds=' + '0' * 31 + '2', ['read', '/yaffs2/firmware.version'])
        self.assertEqual(code, 2)
        self.assertIn('speaks protocol 2.0', out)
        self.assertEqual(sent, [P.HELLO])
        for text in (old, 'XYF1', full):  # every existing command works
            dev = host.Device(Device(text))
            dev.hello()
            self.assertEqual(dev.call(P.SET_DEFAULT, b'a\0')[0], 0)
            self.assertEqual(dev.call(P.SCREEN, b'\0' * 4)[0], 0)
        dev = host.Device(Device(full))
        dev.hello()
        with self.assertRaises(host.Unsupported):
            dev.call(0x06)

    def test_install_and_reboot_default_to_dry_run(self):
        build = verified_build(self)
        called = []
        original = self.host.connect
        self.host.connect = lambda timeout: called.append(1)
        try:
            self.assertEqual(self.host.main(['install', str(build)]), 0)
            self.assertEqual(self.host.main(['reboot']), 0)
        finally:
            self.host.connect = original
        self.assertEqual(called, [])  # never touched the device


class PutChunkTest(unittest.TestCase):
    """Test 034: a PUT_DATA frame must fit the device's 0x800-byte CDC receive ring."""

    def test_put_frames_fit_the_cdc_ring(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location('xyf_host_chunk', ROOT / 'xy_forever/xyf_host.py')
        host = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(host)
        frame = len(P.encode(P.PUT_DATA, 1, bytes(4 + host.PUT_CHUNK)))
        self.assertLess(frame, 0x800)  # one frame in flight fits the ring
        sent = []

        class Recorder(FakeDevice):
            def write(self, data):
                sent.append(len(data))
                FakeDevice.write(self, data)

        dev = host.Device(Recorder())
        self.assertEqual(dev.hello()[0], 0)
        self.assertEqual(dev.stage(bytes(range(256)) * 300), 0)
        self.assertLessEqual(max(sent), frame)


class InstallLogTest(unittest.TestCase):
    """Test 034: INSTALL gave no reply for 180 s. Test 035 forwards the stock writer's log
    frames to the host while it waits for the install."""

    def test_host_collects_log_frames_while_waiting(self):
        from xy_forever import xyf_host as host

        class Logging(FakeDevice):
            def handle(self, kind, payload):
                if kind == P.INSTALL:
                    for i, line in enumerate(
                        (b'xyf install step 4 value 0x0035f488', b'[mmc_tools.cpp:117]: write sectors: 1-128')
                    ):
                        self.out += P.encode(P.LOG | P.REPLY, 0, struct.pack('<II', 0, 1000 + i) + line)
                return FakeDevice.handle(self, kind, payload)

        got = []
        fake = Logging({})
        dev = host.Device(fake, on_log=lambda ms, text: got.append((ms, text)))
        data = bytes(range(256)) * 300
        self.assertEqual(dev.hello()[0], 0)
        self.assertEqual(dev.stage(data), 0)
        self.assertEqual(dev.install(hashlib.sha256(data).digest())[0], 0)
        self.assertEqual(
            got, [(1000, 'xyf install step 4 value 0x0035f488'), (1001, '[mmc_tools.cpp:117]: write sectors: 1-128')]
        )
        self.assertEqual(dev.log, got)
        self.assertEqual(host.INSTALL_TIMEOUT, 900.0)


@pytest.mark.gate
class RangedReadTest(unittest.TestCase):
    """Test 038: ranged READ_FILE brings reports (~1 MB) over USB in small ranges."""

    def test_host_reads_a_large_file_in_ranges(self):
        from xy_forever import xyf_host as host

        self.assertLessEqual(host.READ_RANGE, 1024)  # ranged reads stay small
        report = bytes(range(256)) * 4000 + b'tail'  # 1,024,004 bytes
        fake = FakeDevice({'/yaffs2/xy-forever/log/usb-038-b.bin': report})
        dev = host.Device(fake)
        self.assertEqual(dev.hello()[0], 0)
        self.assertEqual(dev.read_file('/yaffs2/xy-forever/log/usb-038-b.bin')[0], 9)  # whole-file read: too large
        code, data = dev.read_all('/yaffs2/xy-forever/log/usb-038-b.bin')
        self.assertEqual((code, data), (0, report))
        exact = bytes(2 * host.READ_RANGE)  # multiple of the range: one empty range ends it
        fake.files['/yaffs2/x'] = exact
        self.assertEqual(dev.read_all('/yaffs2/x'), (0, exact))


class HoldTest(unittest.TestCase):
    """Test 041 hold: the v2 held service loop is gone; v3 home keeps HOLD (home_pump). The guard
    (GUARD, HANG) is gone."""

    def test_host_commands(self):
        from xy_forever import xyf_host as host

        self.assertEqual(P.HOLD, 0x32)
        for gone in (['guard', 'on'], ['test-hang']):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                host.main(gone)


if __name__ == '__main__':
    unittest.main()
