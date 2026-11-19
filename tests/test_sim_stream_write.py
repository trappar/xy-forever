"""#4: streamed, replace-safe file writes (file.write_begin / write_data / write_end, protocol 1.18) in the
simulator: the bytes go into /yaffs2/xy-forever/tmp/put-<size>-<crc>, are read back, and A renames the file onto its
target in one step. A cut cable, a full storage and a power cut leave the target old or new, never partial, and the
temporary file goes away. Needs clang++ and the private stock 1.2.1 images (skipped without them). No device access."""
import hashlib
from pathlib import Path
import shutil
import struct
import sys
import tempfile
import time
import unittest
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import xyf_host as host                                                   # noqa: E402
from xy_forever import host_write, sim, usb_proto_ids as P               # noqa: E402

UNAVAILABLE = sim.available() or (None if sim.STOCK_OS.is_file() else 'private stock 1.2.1 os.ldr absent')
TARGET = '/yaffs2/user/stream/song.bin'


@unittest.skipIf(UNAVAILABLE, UNAVAILABLE)
class StreamWriteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='xyf-stream-')
        cls.out = sim.build()
        cls.seeded = sim.seed(Path(cls.tmp.name) / 'seed')
        cls.runs = 0

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        type(self).runs += 1
        self.dir = Path(self.tmp.name) / ('run-%d' % self.runs)
        shutil.copytree(self.seeded, self.dir / 'yaffs2')
        self.sim = sim.Simulator(self.dir / 'yaffs2', self.dir / 'bootpart.bin', build_dir=self.out,
                                 log=self.dir / 'log.txt').start()
        self.links = []

    def tearDown(self):
        for link in self.links:
            link.t.close()
        self.sim.stop()

    def connect(self):
        dev = host.Device(host.TcpPort(self.sim.address), on_log=None)
        self.links.append(dev)
        self.assertEqual(dev.hello()[0], 0)
        return dev

    def local(self, path):
        return self.dir / 'yaffs2' / path[len('/yaffs2/'):]

    def uploads(self):
        tmp = self.local('/yaffs2/xy-forever/tmp')
        return sorted(p.name for p in tmp.iterdir() if p.name.startswith('put-')) if tmp.is_dir() else []

    def until(self, predicate, what, timeout=60.0):
        """predicate() on the files, advancing the device clock (B's idle and sweep turns run on it)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            if self.sim.status()['b'] == 'resetting':
                self.sim.advance(100)
            else:
                self.sim.advance(250)
                time.sleep(0.01)
        self.fail('%s not reached within %g s' % (what, timeout))

    def begin(self, dev, path, data, crc=None, sha=None):
        crc = zlib.crc32(data) & 0xffffffff if crc is None else crc
        sha = hashlib.sha256(data).digest() if sha is None else sha
        return dev.call(P.WRITE_BEGIN, path.encode() + b'\0' + struct.pack('<II', len(data), crc) + sha, timeout=30.0)[0]

    def old(self, data=b'old file\n'):
        path = self.local(TARGET)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return data

    # -- the write ------------------------------------------------------------------------------
    def test_a_streamed_write_creates_then_replaces_a_file(self):
        dev = self.connect()
        first = bytes(range(256)) * 40                                    # 10 KiB: ten pieces
        self.assertEqual(host_write.write_stream(dev, TARGET, first), (0, len(first), zlib.crc32(first), 0))
        self.assertEqual(self.local(TARGET).read_bytes(), first)
        second = b'replaced\n' * 300
        code, written, back, error = host_write.write_stream(dev, TARGET, second)
        self.assertEqual((code, written, back, error), (0, len(second), zlib.crc32(second), 0))
        self.assertEqual(self.local(TARGET).read_bytes(), second)
        self.assertEqual(dev.call(P.WRITE_END)[0], 0)                     # a retry after a lost reply: ok again
        self.assertEqual(self.uploads(), [])
        self.assertEqual(host_write.write_stream(dev, TARGET, b''), (0, 0, 0, 0))   # an empty file
        self.assertEqual(self.local(TARGET).read_bytes(), b'')

    def test_order_range_checks_and_refusals(self):
        dev = self.connect()
        old = self.old()
        self.assertEqual(dev.call(P.WRITE_DATA, struct.pack('<I', 0) + b'x')[0], P.S_STATE)
        self.assertEqual(dev.call(P.WRITE_END)[0], P.S_STATE)
        for path in ('/yaffs2/bootloader.dev', '/yaffs2/upgrade/a.bin', '/yaffs2/BOOTLOADER.DEV', '/fat32/a'):
            self.assertEqual(self.begin(dev, path, b'x'), P.S_REFUSED, path)
        data = b'new bytes\n' * 200
        self.assertEqual(self.begin(dev, TARGET, data), 0)
        self.assertEqual(self.uploads(), ['put-%08x-%08x' % (len(data), zlib.crc32(data))])
        self.assertEqual(self.local(TARGET).read_bytes(), old)            # untouched until the end
        self.assertEqual(dev.call(P.WRITE_DATA, struct.pack('<I', 5) + data[:10])[0], P.S_RANGE)
        self.assertEqual(dev.call(P.WRITE_DATA, struct.pack('<I', 0) + data[:1024])[0], 0)
        self.assertEqual(dev.call(P.WRITE_DATA, struct.pack('<I', 0) + data[:1024])[0], 0)   # a resend
        self.assertEqual(dev.call(P.WRITE_END)[0], P.S_STATE)             # not every byte yet
        self.assertEqual(dev.call(P.WRITE_DATA, struct.pack('<I', 1024) + data[1024:])[0], 0)
        self.assertEqual(dev.call(P.WRITE_DATA, struct.pack('<I', len(data)) + b'!')[0], P.S_RANGE)
        self.assertEqual(dev.call(P.WRITE_END)[0], 0)
        self.assertEqual(self.local(TARGET).read_bytes(), data)

    def test_a_wrong_crc_or_sha_or_a_folder_target_leaves_the_target(self):
        dev = self.connect()
        old = self.old()
        data = b'payload\n' * 100
        for crc, sha, status in ((zlib.crc32(data) ^ 1, None, P.S_CRC), (None, bytes(32), P.S_SHA)):
            self.assertEqual(self.begin(dev, TARGET, data, crc, sha), 0)
            self.assertEqual(dev.call(P.WRITE_DATA, struct.pack('<I', 0) + data)[0], 0)
            self.assertEqual(dev.call(P.WRITE_END)[0], status)
            self.assertEqual((self.local(TARGET).read_bytes(), self.uploads()), (old, []))
        folder = TARGET + '.d'
        self.local(folder).mkdir()
        code, _, _, error = host_write.write_stream(dev, folder, data)
        self.assertEqual((code, error), (P.S_STORAGE, 21))                # A: a folder is never replaced
        self.assertTrue(self.local(folder).is_dir())
        self.assertEqual(self.uploads(), [])

    def test_larger_than_the_staging_area(self):
        dev = self.connect()
        data = bytes(range(251)) * ((P.MAX_STAGE + 4096) // 251 + 1)       # above stage= (7 MiB)
        self.assertGreater(len(data), P.MAX_STAGE)
        self.assertEqual(host_write.write_stream(dev, TARGET, data)[:2], (0, len(data)))
        self.assertEqual(self.local(TARGET).read_bytes(), data)

    # -- faults ---------------------------------------------------------------------------------
    def test_cable_pull_mid_write_leaves_the_old_file_and_removes_the_upload(self):
        dev = self.connect()
        old = self.old()
        data = b'half sent\n' * 500
        self.assertEqual(self.begin(dev, TARGET, data), 0)
        self.assertEqual(dev.call(P.WRITE_DATA, struct.pack('<I', 0) + data[:1024])[0], 0)
        dev.t.close()
        self.links.remove(dev)
        self.until(lambda: not self.uploads(), 'the abandoned upload removed')
        self.assertEqual(self.local(TARGET).read_bytes(), old)
        dev = self.connect()                                              # the service goes on
        self.assertEqual(host_write.write_stream(dev, TARGET, data)[0], 0)
        self.assertEqual(self.local(TARGET).read_bytes(), data)

    def test_full_storage_and_a_failed_write(self):
        dev = self.connect()
        old = self.old()
        data = bytes(5000)
        for fault, value, sent in (('space', 2500, 2048), ('fail_write', 2, 1024)):   # the 3rd / 2nd piece fails
            self.sim.set(fault, value)
            code, written, _, _ = host_write.write_stream(dev, TARGET, data)
            self.assertEqual((code, written), (P.S_STORAGE, sent))
            self.sim.set('space', -1)
            self.assertEqual((self.local(TARGET).read_bytes(), self.uploads()), (old, []))
        self.assertEqual(host_write.write_stream(dev, TARGET, data)[0], 0)

    def test_power_cut_mid_write_then_the_sweep(self):
        dev = self.connect()
        old = self.old()
        data = b'cut short\n' * 500
        self.assertEqual(self.begin(dev, TARGET, data), 0)
        self.sim.set('cut_after', 2)                                      # the second piece's write
        with self.assertRaises((OSError, TimeoutError)):
            host_write.write_stream(dev, TARGET, data)
        self.until(lambda: self.sim.status()['cuts'] == 1 and self.sim.status()['listening'], 'power cut, restart')
        self.assertEqual(self.local(TARGET).read_bytes(), old)
        self.until(lambda: not self.uploads(), 'the USB start sweep removing the upload')
        self.assertEqual(self.local(TARGET).read_bytes(), old)

    def test_power_cut_right_after_the_rename_keeps_the_new_file(self):
        dev = self.connect()
        self.old()
        data = b'renamed then cut\n' * 100
        self.sim.set('rename_fault_kind', 2)
        self.sim.set('rename_fault', 1)
        with self.assertRaises((OSError, TimeoutError)):
            host_write.write_stream(dev, TARGET, data)
        self.until(lambda: self.sim.status()['cuts'] == 1 and self.sim.status()['listening'], 'power cut, restart')
        self.assertEqual((self.local(TARGET).read_bytes(), self.uploads()), (data, []))


if __name__ == '__main__':
    unittest.main()
