"""Faster serial transfers (protocol 1.33) in the simulator: file.read_stream keeps a file open between pieces, and with
hello pipe=4 the hosts send max_data pieces with several requests in flight (file.read_stream, file.write_data). Older
devices (no file.read_stream, pipe=1 on the stock serial fallback) keep the 1 KiB path. A damaged, cut short or aborted
transfer never passes as whole, and a lost reply's request is sent again. Needs clang++ and the private stock 1.2.1
images (skipped without them). No device access."""

import hashlib
import os
import shutil
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

import pytest

from xy_forever import host_write, sim
from xy_forever import usb_proto as P
from xy_forever import xyf_host as host

ids = P.ids
UNAVAILABLE = sim.available() or (None if sim.STOCK_OS.is_file() else 'private stock 1.2.1 os.ldr absent')
TARGET = '/yaffs2/user/fast/image.bin'
SIZE = 2_373_336  # the stock os.ldr's size: what xyf stock-os and xyf dev move


def old_client(dev):
    """The host as before 1.33: no file.read_stream, one request at a time."""
    dev.hello_info.cmds &= ~(1 << ids.READ_STREAM)
    dev.hello_info.keys.pop('pipe')


class CutReplies:
    """The transport with the last `cut` bytes of chosen replies lost (Web Serial's lost tails, #306): replies by number
    of the given type, from 1."""

    def __init__(self, port, kind, cuts):
        self.port, self.kind, self.cuts = port, kind | P.REPLY, dict(cuts)
        self.parser, self.seen = P.Parser(), 0

    def write(self, data):
        self.port.write(data)

    def read(self, timeout):
        out = b''
        for kind, seq, body in self.parser.feed(self.port.read(timeout)):
            frame = P.encode(kind, seq, body)
            if kind == self.kind:
                self.seen += 1
                frame = frame[: len(frame) - self.cuts.get(self.seen, 0)]
            out += frame
        return out

    def close(self):
        self.port.close()


@unittest.skipIf(UNAVAILABLE, UNAVAILABLE)
class FastSerialTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='xyf-fast-serial-')
        cls.out = sim.build()
        cls.seeded = sim.seed(Path(cls.tmp.name) / 'seed')
        cls.runs = 0

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def start(self, stock_serial=False):
        type(self).runs += 1
        self.dir = Path(self.tmp.name) / ('run-%d' % self.runs)
        shutil.copytree(self.seeded, self.dir / 'yaffs2')
        machine = sim.Simulator(
            self.dir / 'yaffs2', self.dir / 'bootpart.bin', binary=self.out, log=self.dir / 'log.txt'
        )
        if stock_serial:  # the device's limits on the stock serial fallback (usb_service_transport.ipp pipe_depth)
            os.environ['XYF_SIM_STOCK_SERIAL'] = '1'
        try:
            self.sim = machine.start()
        finally:
            os.environ.pop('XYF_SIM_STOCK_SERIAL', None)
        self.addCleanup(self.sim.stop)

    def connect(self):
        dev = host.Device(host.TcpPort(self.sim.address), on_log=None)
        self.addCleanup(dev.t.close)
        self.assertEqual(dev.hello()[0], 0)
        return dev

    def local(self, path):
        return self.dir / 'yaffs2' / path[len('/yaffs2/') :]

    def uploads(self):
        tmp = self.local('/yaffs2/xy-forever/tmp')
        return sorted(p.name for p in tmp.iterdir() if p.name.startswith('put-')) if tmp.is_dir() else []

    def measured(self, work):
        """work()'s result, the device time it took (ms) and the link messages B sent A for it."""
        before = self.sim.status()
        result = work()
        after = self.sim.status()
        return result, after['clock_ms'] - before['clock_ms'], after['to_a'] - before['to_a']

    def sent(self, dev):
        """The request types dev sends from now on, in order."""
        kinds, write = [], dev.t.write

        def spy(data):
            kinds.extend(kind for kind, _, _ in P.Parser().feed(data))
            write(data)

        dev.t.write = spy
        return kinds

    @pytest.mark.sim
    def test_a_2_3_mb_image_moves_with_a_fraction_of_the_link_traffic(self):
        """Kept: the throughput measure: a 2.3 MB image written and read back whole, both ways, the new path
        (max_data pieces, pipe in flight, the file kept open) against the 1.32 path on the same device; device time and
        B's requests to A, which the simulator counts (it has no USB timing: the device's own figures are measured on
        it)."""
        self.start()
        data = hashlib.shake_256(b'xyf').digest(SIZE)
        figures = {}
        for path in ('new', 'old'):
            dev = self.connect()
            if path == 'old':
                old_client(dev)
            self.assertEqual(dev.hello_info.pipe, ids.MAX_PIPE if path == 'new' else 1)
            kinds = self.sent(dev)
            code, write_ms, write_msgs = self.measured(lambda: host_write.put(dev, data, TARGET, say=lambda _: None))
            self.assertEqual(code, 0)
            (code, back), read_ms, read_msgs = self.measured(lambda: dev.read_all(TARGET, limit=SIZE + 8192))
            self.assertEqual((code, back == data), (0, True))
            self.assertEqual(self.local(TARGET).read_bytes(), data)
            figures[path] = write_ms, write_msgs, read_ms, read_msgs
            stream = ids.READ_STREAM in kinds
            self.assertEqual((stream, ids.READ_FILE in kinds), (path == 'new', path == 'old'))
            dev.t.close()
        new, old = figures['new'], figures['old']
        print(
            '2.3 MB (device ms, link messages): write %s, read %s; 1.32 path: write %s, read %s'
            % (new[:2], new[2:], old[:2], old[2:])
        )
        self.assertLess(new[3] * 10, old[3])  # a read: one request to A per 4 KiB, not six per 1 KiB
        self.assertLess(new[2] * 3, old[2])
        self.assertLess(new[1] * 2, old[1])  # a write (with its read-back): 4 KiB storage writes and reads

    @pytest.mark.sim
    def test_the_stock_serial_fallback_and_older_hosts_keep_1_kib_one_at_a_time(self):
        """Kept: compatibility: on the stock serial fallback the device says pipe=1 and refuses a 4 KiB piece; the
        hosts then read and write 1 KiB pieces, one at a time, as an older host does with any device."""
        self.start(stock_serial=True)
        dev = self.connect()
        self.assertEqual(dev.hello_info.pipe, 1)
        self.assertIn(';pipe=1;', dev.hello_info.text)
        data = os.urandom(5000)
        self.assertEqual(host_write.put(dev, data, TARGET, say=lambda _: None), 0)
        payload = TARGET.encode() + b'\0'
        self.assertEqual(dev.call(ids.READ_STREAM, payload + struct.pack('<II', 0, 4096))[0], ids.S_BAD_REQUEST)
        kinds = self.sent(dev)
        self.assertEqual(dev.read_all(TARGET), (0, data))
        self.assertEqual(kinds, [ids.READ_STREAM] * 5)  # 1 KiB pieces, the last short
        del kinds[:]
        self.assertEqual(host_write.put(dev, data[::-1], TARGET, say=lambda _: None), 0)
        self.assertEqual(kinds.count(ids.WRITE_DATA), 5)
        self.assertEqual(self.local(TARGET).read_bytes(), data[::-1])

    @pytest.mark.sim
    def test_lost_reply_tails_damaged_cut_short_and_aborted_transfers(self):
        """Kept: integrity with requests in flight: a reply whose tail is lost (#306) is asked again and the bytes come
        whole; a damaged piece or a write cut short never replaces the target (file.write_end's CRC-32 and SHA-256);
        an aborted read leaves the device answering and the file readable again."""
        self.start()
        dev = self.connect()
        data = os.urandom(10 * ids.MAX_DATA + 99)
        self.assertEqual(host_write.put(dev, data, TARGET, say=lambda _: None), 0)
        kinds = self.sent(dev)
        dev.t = CutReplies(dev.t, ids.READ_STREAM, {2: 40, 7: 5})  # two pieces' replies cut short
        self.assertEqual(dev.read_all(TARGET), (0, data))
        self.assertEqual(kinds.count(ids.READ_STREAM), 11 + 3 + 2)  # the pieces, the three past the end, two resends
        dev.t = dev.t.port
        dev.t = CutReplies(dev.t, ids.WRITE_DATA, {3: 6})
        other = data[::-1]
        self.assertEqual(host_write.put(dev, other, TARGET, say=lambda _: None), 0)  # the lost reply's piece again
        dev.t = dev.t.port
        self.assertEqual(self.local(TARGET).read_bytes(), other)

        # A damaged piece: the announced checks fail at the end; the target and the device's tmp/ stay clean.
        crc, sha = zlib.crc32(data) & 0xFFFFFFFF, hashlib.sha256(data).digest()
        head = TARGET.encode() + b'\0' + struct.pack('<II', len(data), crc) + sha
        self.assertEqual(dev.call(ids.WRITE_BEGIN, head, timeout=30.0)[0], 0)
        pieces = [struct.pack('<I', at) + data[at : at + ids.MAX_DATA] for at in range(0, len(data), ids.MAX_DATA)]
        pieces[3] = pieces[3][:100] + bytes([pieces[3][100] ^ 1]) + pieces[3][101:]
        self.assertEqual([code for code, _ in dev.pipeline(ids.WRITE_DATA, pieces, ids.MAX_PIPE, 30.0)], [0] * 11)
        self.assertEqual(dev.call(ids.WRITE_END, timeout=60.0)[0], ids.S_CRC)
        # Cut short: file.write_end refuses it (state) and the target stays as it was.
        self.assertEqual(dev.call(ids.WRITE_BEGIN, head, timeout=30.0)[0], 0)
        list(dev.pipeline(ids.WRITE_DATA, pieces[:5], ids.MAX_PIPE, 30.0))
        self.assertEqual(dev.call(ids.WRITE_END, timeout=60.0)[0], ids.S_STATE)
        self.assertEqual(self.local(TARGET).read_bytes(), other)

        # An aborted read: the host stops after two pieces; its file is closed on the next request (or after 1 s).
        replies = dev.pipeline(
            ids.READ_STREAM,
            (TARGET.encode() + b'\0' + struct.pack('<II', at, ids.MAX_DATA) for at in range(0, 1 << 20, ids.MAX_DATA)),
            ids.MAX_PIPE,
            15.0,
        )
        self.assertEqual([next(replies)[1], next(replies)[1]], [other[:4096], other[4096:8192]])
        replies.close()  # the replies to the requests still out come later, stale: dropped
        self.sim.advance(6000)  # past the read stream's idle close (1 s) and the cut-short upload's (5 s)
        self.assertEqual(dev.read_all(TARGET), (0, other))
        self.assertEqual(self.uploads(), [])
