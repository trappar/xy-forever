"""#7's read-only diagnostic and actual host runner. Simulator/fakes only; never a device."""
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import time
import unittest
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'scripts'), str(ROOT / 'tests')]
import test_simulator as T  # noqa: E402
from xy_forever import usb_proto_ids as P  # noqa: E402

spec = importlib.util.spec_from_file_location('boot_probe_script', ROOT / 'scripts/boot_probe.py')
B = importlib.util.module_from_spec(spec)
spec.loader.exec_module(B)


class Fake:
    def __init__(self, replies):
        self.replies, self.calls, self.hello_info = iter(replies), [], object()

    def call(self, kind, payload, timeout):
        self.calls.append((kind, struct.unpack_from('<I', payload)[0], timeout))
        r = next(self.replies)
        if isinstance(r, Exception):
            raise r
        return r


class RunnerTests(unittest.TestCase):
    def test_newer_reply_is_refused_and_transport_timeout_sends_no_recovery(self):
        with self.assertRaisesRegex(B.ProbeError, 'version'):
            B.call(Fake([(0, struct.pack('<I', 2))]), B.BEGIN)
        dev = Fake([(0, struct.pack('<III', 1, 0, 0)), TimeoutError('no answer')])
        with tempfile.TemporaryDirectory() as tmp:
            result = B.run(dev, Path(tmp))
        self.assertTrue(result['link_lost'])
        self.assertFalse(result['restored'])
        self.assertEqual([x[1] for x in dev.calls], [B.BEGIN, B.HEADER])

    def test_responsive_read_error_restores_but_link_error_stops(self):
        for error, expected in ((P.S_STORAGE, [B.BEGIN, B.HEADER, B.END]),
                                (P.S_LINK, [B.BEGIN, B.HEADER])):
            dev = Fake([(0, struct.pack('<III', 1, 0, 0)), (error, struct.pack('<I', 1)),
                        (0, struct.pack('<I', 1))])
            with tempfile.TemporaryDirectory() as tmp:
                result = B.run(dev, Path(tmp))
            self.assertEqual([x[1] for x in dev.calls], expected)
            self.assertEqual(result['restored'], error == P.S_STORAGE)
            self.assertFalse(result['go'])


@unittest.skipIf(T.UNAVAILABLE, T.UNAVAILABLE)
class BootProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        T.SimulatorTest.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        T.SimulatorTest.tearDownClass.__func__(cls)

    setUp = T.SimulatorTest.setUp
    tearDown = T.SimulatorTest.tearDown
    connect = T.SimulatorTest.connect
    wait = T.SimulatorTest.wait

    def seed_image(self):
        self.image = bytes(range(251)) * 5
        head = struct.pack('<IIIBB', 3, len(self.image), zlib.crc32(self.image), 1, 0).ljust(512, b'\0')
        self.partition_bytes = head + self.image.ljust(self.sim.capacity - 512, b'\0') + bytes(self.sim.capacity)
        (self.dir / 'bootpart.bin').write_bytes(self.partition_bytes)

    def begin(self):
        self.dev = self.connect()
        deadline = time.monotonic() + 10
        while True:
            try:
                return B.call(self.dev, B.BEGIN)
            except B.ProbeError as error:
                if 'busy' not in str(error) or time.monotonic() > deadline:
                    raise
                time.sleep(.02)

    def test_actual_runner_reads_both_headers_and_image_without_writing(self):
        self.seed_image()
        dev = self.connect()
        result = B.run(dev, self.dir)
        self.assertNotIn('error', result, result)
        self.assertTrue(result['restored'], result)
        self.assertEqual(result['active_partition'], 1)
        self.assertEqual(result['image_bytes'], len(self.image))
        self.assertEqual(result['image_sha256'], hashlib.sha256(self.image).hexdigest())
        self.assertEqual((self.dir / 'active-image.bin').read_bytes(), self.image)
        self.assertEqual(len(result['headers']), 2)
        self.assertEqual((self.dir / 'bootpart.bin').read_bytes(), self.partition_bytes)
        self.assertEqual(self.sim.status()['partition'], 0)
        self.assertEqual(self.sim.status()['boot_writes'], 0)
        self.assertEqual(dev.read_file(T.BASE + 'state-a.txt')[0], 0)

    def test_final_cli_records_private_evidence_and_returns_restored(self):
        self.seed_image()
        with tempfile.TemporaryDirectory(prefix='boot-probe-cli-', dir=B.paths.SESSIONS) as tmp:
            out = Path(tmp) / 'evidence'
            r = subprocess.run([sys.executable, str(ROOT / 'scripts/boot_probe.py'), '--sim', self.sim.address,
                                '--out', str(out)], capture_output=True, text=True, timeout=30)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            result = json.loads((out / 'result.json').read_text())
            self.assertTrue(result['restored'], result)
            self.assertEqual(result['image_sha256'], hashlib.sha256(self.image).hexdigest())
        self.assertEqual(self.sim.status()['partition'], 0)

    def test_probe_refuses_writes_trim_files_and_ui_or_serial_boot(self):
        self.seed_image()
        self.begin()
        B.call(self.dev, B.HEADER, 1)
        for cls, op, arg, body in ((T.WRITE_CLASS, 0x15, 0, bytes(512)),
                                   (T.WRITE_CLASS, 0x16, 1, bytes(512)),
                                   (T.PARTITION_CLASS, 0x14, 0, b''),
                                   (T.PATH_CLASS, 0x29, 0x8301, T.raw_path(T.BASE + 'probe-forbidden'))):
            self.assertEqual(self.sim.ask_a(cls, op, arg, body=body), (-1, T.EACCES))
        self.assertEqual(self.dev.read_file(T.BASE + 'state-a.txt')[0], P.S_BUSY)
        self.assertEqual(self.dev.call(P.REBOOT, struct.pack('<I', 0))[0], P.S_BUSY)
        self.assertEqual(self.dev.hello()[0], 0)
        self.assertEqual((self.dir / 'bootpart.bin').read_bytes(), self.partition_bytes)
        self.assertFalse((self.dir / 'yaffs2/xy-forever/probe-forbidden').exists())
        self.sim.advance(1000)  # keepalive uses the shared slot between reads; it must not change the probe
        self.assertEqual(self.dev.hello()[0], 0)
        self.assertEqual(B.call(self.dev, B.SECTOR, 1, 1)[:len(self.image[:512])], self.image[:512])
        B.call(self.dev, B.END)
        self.assertEqual(self.sim.status()['partition'], 0)

    def test_failed_select_and_failed_restore_keep_partition_gate_closed(self):
        self.seed_image()
        self.begin()
        self.sim.set('fail_select', 1)
        with self.assertRaisesRegex(B.ProbeError, 'storage'):
            B.call(self.dev, B.HEADER, 1)
        self.assertEqual(self.sim.status()['partition'], 1)  # hardware switched before the failure
        self.assertEqual(self.sim.ask_a(T.PATH_CLASS, 0x26, 0, body=T.raw_path(T.BASE + 'blocked')), (-1, T.EACCES))
        self.sim.set('fail_select', 1)
        with self.assertRaisesRegex(B.ProbeError, 'storage'):
            B.call(self.dev, B.END)
        self.assertEqual(self.dev.read_file(T.BASE + 'state-a.txt')[0], P.S_BUSY)
        self.assertEqual(self.sim.status()['partition'], 0)  # even failed restore may have switched
        self.assertEqual(self.sim.ask_a(T.PATH_CLASS, 0x21, 0x31504202, body=T.raw_path('/yaffs2')), (-1, T.EBUSY))
        self.assertEqual(self.sim.ask_a(T.PATH_CLASS, 0x26, body=T.raw_path(T.BASE + 'blocked')), (-1, T.EACCES))
        B.call(self.dev, B.END)
        self.assertEqual(self.sim.status()['partition'], 0)
        self.assertEqual(self.dev.read_file(T.BASE + 'state-a.txt')[0], 0)

    def test_newer_request_and_out_of_range_reads_do_not_change_partition(self):
        dev = self.connect()
        dev.hello_info = None
        self.assertEqual(dev.call(B.COMMAND, struct.pack('<II', B.BEGIN, 2))[0], P.S_UNSUPPORTED)
        self.assertEqual(self.sim.status()['partition'], 0)
        self.dev = dev
        # Wait for the ordinary boot sweep before beginning.
        self.begin()
        for op, values in ((B.HEADER, (3,)), (B.SECTOR, (1, 1))):
            with self.assertRaisesRegex(B.ProbeError, 'range'):
                B.call(self.dev, op, *values)
        self.assertEqual(self.sim.status()['partition'], 0)
        B.call(self.dev, B.END)

    def test_silent_a_returns_link_without_resetting_b_or_resuming_files(self):
        self.seed_image()
        self.begin()
        self.sim.set('a_silent', 1)
        with self.assertRaisesRegex(ConnectionError, 'link'):
            B.call(self.dev, B.HEADER, 1)
        self.assertEqual(self.dev.hello()[0], 0)
        self.assertEqual(self.dev.read_file(T.BASE + 'state-a.txt')[0], P.S_BUSY)
        self.assertEqual(self.sim.status()['b_boots'], 1)
