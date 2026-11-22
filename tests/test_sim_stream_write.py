"""#4: streamed, replace-safe file writes (file.write_begin / write_data / write_end, protocol 1.18) in the
simulator: the bytes go into /yaffs2/xy-forever/tmp/put-<size>-<crc>, are read back, and A renames the file onto its
target in one step. A cut cable, a full storage and a power cut leave the target old or new, never partial, and the
temporary file goes away. Needs clang++ and the private stock 1.2.1 images (skipped without them). No device access."""

import hashlib
import shutil
import struct
import tempfile
import time
import unittest
import zlib
from pathlib import Path

import pytest

from xy_forever import bounds, host_write, sim
from xy_forever import usb_proto_ids as P
from xy_forever import xyf_host as host
from xy_forever.startup_ready import wait_startup_ready

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
        self.sim = sim.Simulator(
            self.dir / 'yaffs2', self.dir / 'bootpart.bin', binary=self.out, log=self.dir / 'log.txt'
        ).start()
        self.links = []

    def tearDown(self):
        for link in self.links:
            link.t.close()
        self.sim.stop()

    def connect(self, startup_ready=False):
        dev = host.Device(host.TcpPort(self.sim.address), on_log=None)
        self.links.append(dev)
        self.assertEqual(dev.hello()[0], 0)
        if startup_ready:
            wait_startup_ready(
                dev.hello_info.text,
                lambda remaining: dev.call(P.STORAGE_SPACE, struct.pack('<I', 1), timeout=remaining)[0],
            )
        return dev

    def local(self, path):
        return self.dir / 'yaffs2' / path[len('/yaffs2/') :]

    def uploads(self):
        tmp = self.local('/yaffs2/xy-forever/tmp')
        return sorted(p.name for p in tmp.iterdir() if p.name.startswith('put-')) if tmp.is_dir() else []

    def until(self, predicate, what, timeout=bounds.WAIT_S):
        """predicate() on the files, advancing the device clock (B's idle and sweep turns run on it)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            if self.sim.status()['b'] == 'resetting':
                self.sim.advance(100)
            else:
                self.sim.advance(250)
                self.sim.advance(10)
        self.fail('%s not reached within %g s' % (what, timeout))

    def begin(self, dev, path, data, crc=None, sha=None):
        crc = zlib.crc32(data) & 0xFFFFFFFF if crc is None else crc
        sha = hashlib.sha256(data).digest() if sha is None else sha
        return dev.call(P.WRITE_BEGIN, path.encode() + b'\0' + struct.pack('<II', len(data), crc) + sha, timeout=30.0)[
            0
        ]

    def old(self, data=b'old file\n'):
        path = self.local(TARGET)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return data

    # -- the write ------------------------------------------------------------------------------

    # -- faults ---------------------------------------------------------------------------------
    @pytest.mark.sim
    def test_cable_pull_mid_write_leaves_the_old_file_and_removes_the_upload(self):
        """Kept: USB fault mid write: the old file stays, the temporary one goes."""
        dev = self.connect(startup_ready=True)
        old = self.old()
        data = b'half sent\n' * 500
        self.assertEqual(self.begin(dev, TARGET, data), 0)
        self.assertEqual(dev.call(P.WRITE_DATA, struct.pack('<I', 0) + data[:1024])[0], 0)
        dev.t.close()
        self.links.remove(dev)
        self.until(lambda: not self.uploads(), 'the abandoned upload removed')
        self.assertEqual(self.local(TARGET).read_bytes(), old)
        dev = self.connect()  # the service goes on
        self.assertEqual(host_write.write_stream(dev, TARGET, data)[0], 0)
        self.assertEqual(self.local(TARGET).read_bytes(), data)

    @pytest.mark.sim
    def test_power_cut_mid_write_then_the_sweep(self):
        """Kept: Power cut mid write: the old file stays and the sweep removes the leftover."""
        dev = self.connect(startup_ready=True)
        old = self.old()
        data = b'cut short\n' * 500
        self.assertEqual(self.begin(dev, TARGET, data), 0)
        self.sim.set('cut_after', 2)  # the second piece's write
        with self.assertRaises((OSError, TimeoutError)):
            host_write.write_stream(dev, TARGET, data)
        self.until(lambda: self.sim.status()['cuts'] == 1 and self.sim.status()['listening'], 'power cut, restart')
        self.assertEqual(self.local(TARGET).read_bytes(), old)
        self.until(lambda: not self.uploads(), 'the USB start sweep removing the upload')
        self.assertEqual(self.local(TARGET).read_bytes(), old)

    @pytest.mark.sim
    def test_power_cut_right_after_the_rename_keeps_the_new_file(self):
        """Kept: Power cut after the rename: the new file is kept."""
        dev = self.connect(startup_ready=True)
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
