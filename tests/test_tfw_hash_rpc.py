"""The keyless TFW hash job through processor A's real simulator request path."""
import hashlib
from pathlib import Path
import struct
import tempfile
import time
import unittest

from test_tfw import container
from xy_forever import paths, sim, usb_proto_ids as ids


UNAVAILABLE = sim.available() or (None if sim.STOCK_OS.is_file() else 'private stock 1.2.1 os.ldr absent')
PATH_CLASS, PARTITION_CLASS = 0x119076B4, 0x11907694
TAG, START, STATUS, CANCEL, DIGEST = 0x01544800, 1, 2, 3, 0x10
DIR = '/yaffs2/xy-forever/packages/'


@unittest.skipIf(UNAVAILABLE, UNAVAILABLE)
class TfwHashRpcTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='xyf-tfw-hash-', dir=paths.SESSIONS)
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name)
        root = sim.seed(directory / 'yaffs2')
        self.packages = root / 'xy-forever' / 'packages'
        self.packages.mkdir()
        self.sim = sim.Simulator(root, directory / 'bootpart.bin', log=directory / 'log.txt').start()
        self.addCleanup(self.sim.stop)
        self.command(3)  # command_item, the read-only file engine

    def command(self, item):
        word = 0x434D0000 | item << 8 | 1
        reply = self.sim.ask_a(PARTITION_CLASS, 0x18, word)
        self.assertIsNotNone(reply)
        self.assertEqual(reply[0] & 0xffffffff, ~word & 0xffffffff)

    def request(self, operation, name, tag=TAG):
        return self.sim.ask_a(PATH_CLASS, 0x21, tag | operation, body=(DIR + name).encode() + b'\0')

    def open_file(self, name):
        fd, error = self.sim.ask_a(PATH_CLASS, 0x29, body=(DIR + name).encode() + b'\0')
        self.assertGreaterEqual(fd, 0)
        self.assertEqual(error, 0)
        return fd

    def close_file(self, fd):
        self.assertEqual(self.sim.ask_a(PARTITION_CLASS, 0x2D, fd), (0, 0))

    def free_fd(self, name):
        fd = self.open_file(name)
        self.close_file(fd)
        return fd

    def start(self, name, content):
        (self.packages / name).write_bytes(content)
        fd = self.free_fd(name)
        self.assertEqual(self.request(START, name), (ids.S_OK, len(content)))
        return fd

    def wait_status(self, name):
        deadline = time.monotonic() + 5
        previous = 0
        while time.monotonic() < deadline:
            code, value = self.request(STATUS, name)
            if code != ids.S_BUSY:
                return code, value
            self.assertGreaterEqual(value, previous)
            previous = value
        self.fail('TFW hash never reached a terminal state')

    def test_two_wrappers_payload_only_exact_end_and_no_otp(self):
        before = self.sim.status()
        for short in (False, True):
            with self.subTest(short=short):
                name = 'valid.tfw'
                data = container(bytes(range(256)) * 83, short=short)
                fd = self.start(name, data)
                self.assertEqual(self.request(STATUS, 'other.tfw'), (ids.S_STATE, 0))
                self.assertEqual(self.request(CANCEL, 'other.tfw'), (ids.S_STATE, 0))
                self.assertEqual(self.request(START, name), (ids.S_BUSY, 0))
                self.assertEqual(self.request(STATUS, name, tag=0x02544800), (ids.S_UNSUPPORTED, 1))
                self.assertEqual(self.wait_status(name), (ids.S_OK, len(data)))
                self.assertEqual(self.free_fd(name), fd)
                digest = []
                for i in range(8):
                    code, value = self.request(DIGEST + i, name)
                    self.assertEqual(code, ids.S_OK)
                    digest.append(value)
                expected = hashlib.sha256(data[0x340 if short else 0x380:]).digest()
                self.assertEqual(struct.pack('<8I', *digest), expected)
                self.assertEqual(self.request(CANCEL, name), (ids.S_OK, 0))
                self.assertEqual(self.request(DIGEST, name), (ids.S_STATE, 0))
        after = self.sim.status()
        for field in ('a_otp_reads', 'a_otp_inits'):
            self.assertEqual(after[field], before[field])

    def test_size_and_path_validation_and_error_cleanup(self):
        data = container(b'x' * 5000)
        (self.packages / 'malformed.tfw').write_bytes(b'nothing here')
        (self.packages / 'wrong-sku.tfw').write_bytes(container(b'x', sku=0))
        (self.packages / 'oversized.tfw').write_bytes(data[:0x118] + b'\xff' * 4 + data[0x11c:])
        for name in ('malformed.tfw', 'wrong-sku.tfw', 'oversized.tfw', '../outside.tfw'):
            with self.subTest(name=name):
                self.assertEqual(self.request(START, name), (ids.S_BAD_REQUEST, 0))
        self.assertEqual(self.request(START, 'missing.tfw'), (ids.S_NOT_FOUND, 0))
        self.assertEqual(self.sim.ask_a(PATH_CLASS, 0x21, TAG | START), (ids.S_BAD_REQUEST, 0))
        self.assertEqual(self.sim.ask_a(PATH_CLASS, 0x21, TAG | START, body=b'/yaffs2/upgrade/x.tfw\0'),
                         (ids.S_BAD_REQUEST, 0))
        for cut in (data[:500], data[:-1], data + b'!'):
            (self.packages / 'bad-end.tfw').write_bytes(cut)
            self.assertEqual(self.request(START, 'bad-end.tfw'), (ids.S_OK, len(data)))
            self.assertEqual(self.wait_status('bad-end.tfw'), (ids.S_BAD_REQUEST, 0))
            self.assertEqual(self.request(DIGEST, 'bad-end.tfw'), (ids.S_STATE, 0))
            self.assertEqual(self.request(CANCEL, 'bad-end.tfw'), (ids.S_OK, 0))
        self.command(10)  # file_write_item
        self.assertEqual(self.request(START, 'bad-end.tfw'), (ids.S_BUSY, 0))

    def test_cancel_lease_and_engine_close_release_an_open_reader(self):
        data = container(b'x' * (32 * 1024 * 1024), index=0xfe)
        (self.packages / 'long.tfw').write_bytes(data)
        fd = self.open_file('long.tfw')
        self.assertEqual(self.request(START, 'long.tfw'), (ids.S_BUSY, 0))
        self.close_file(fd)

        self.assertEqual(self.request(START, 'long.tfw'), (ids.S_OK, len(data)))
        code, progress = self.request(STATUS, 'long.tfw')
        self.assertEqual(code, ids.S_BUSY)
        self.assertGreaterEqual(progress, 320)
        self.assertLess(progress, len(data))
        self.assertEqual(self.request(CANCEL, 'long.tfw'), (ids.S_OK, 0))
        self.assertEqual(self.request(STATUS, 'long.tfw'), (ids.S_STATE, 0))
        self.assertEqual(self.free_fd('long.tfw'), fd)

        self.assertEqual(self.request(START, 'long.tfw'), (ids.S_OK, len(data)))
        self.assertEqual(self.sim.ask_a(PARTITION_CLASS, 9), (1, 0))
        self.assertEqual(self.request(STATUS, 'long.tfw'), (ids.S_STATE, 0))
        self.assertEqual(self.sim.ask_a(PARTITION_CLASS, 8), (0, 0))
        self.assertEqual(self.free_fd('long.tfw'), fd)

        self.assertEqual(self.request(START, 'long.tfw'), (ids.S_OK, len(data)))
        self.sim.advance(11000)
        self.assertEqual(self.request(STATUS, 'long.tfw'), (ids.S_STATE, 0))
        self.assertEqual(self.free_fd('long.tfw'), fd)
        self.assertEqual(self.request(START, 'long.tfw'), (ids.S_OK, len(data)))
        self.assertEqual(self.request(CANCEL, 'long.tfw'), (ids.S_OK, 0))
