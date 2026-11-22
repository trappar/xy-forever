"""xyf_host.py's serial transport (xy_forever/transport.py SerialPort) on a fake non-blocking tty: a ready
poll followed by EAGAIN is no data yet (issue #128), writes survive EAGAIN and partial writes, and a whole
stage completes with its data intact. The call's own timeout still applies. On a pty: a port that does
not open, drain or answer costs no more than the caller's timeout, and a close never waits (issue #118)."""
import os
from pathlib import Path
import random
import select
import struct
import sys
import time
import unittest
from unittest import mock
import zlib

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from xy_forever import transport  # noqa: E402
from xy_forever import usb_proto as P  # noqa: E402
import xyf_host  # noqa: E402


class FakeDevice:
    """The device's side of stage.begin/data/end: keeps the staged bytes, answers every frame."""

    def __init__(self):
        self.parser = P.Parser()
        self.replies = bytearray()
        self.staged = bytearray()
        self.crc = None
        self.ended = None

    def receive(self, data):
        for kind, seq, body in self.parser.feed(data):
            code = 0
            if kind == P.PUT_BEGIN:
                size, self.crc = struct.unpack_from('<II', body)
                self.staged = bytearray(size)
            elif kind == P.PUT_DATA:
                offset = struct.unpack_from('<I', body)[0]
                self.staged[offset:offset + len(body) - 4] = body[4:]
            elif kind == P.PUT_END:
                self.ended = zlib.crc32(self.staged) & 0xffffffff == self.crc
                code = 0 if self.ended else 1
            self.replies += P.encode(kind | P.REPLY, seq, struct.pack('<I', code))


class FakeTty:
    """os and select for one non-blocking tty fd: select always reports ready (the readiness macOS gave
    while another process shared the port); read raises EAGAIN every read_eagain-th call, and when nothing
    is queued; write raises EAGAIN every write_eagain-th call and otherwise takes at most write_max bytes."""
    FD = 1000

    def __init__(self, device, read_eagain=0, write_eagain=0, write_max=1 << 20):
        self.device = device
        self.read_eagain, self.write_eagain, self.write_max = read_eagain, write_eagain, write_max
        self.reads = self.writes = 0
        self.read_errors = self.write_errors = self.partial = 0

    def select(self, r, w, x, timeout):
        return list(r), list(w), []

    def read(self, fd, n):
        assert fd == self.FD
        self.reads += 1
        queued = self.device.replies if self.device else b''
        if not queued or (self.read_eagain and self.reads % self.read_eagain == 0):
            self.read_errors += 1
            raise BlockingIOError(35, 'Resource temporarily unavailable')
        data = bytes(queued[:n])
        del queued[:n]
        return data

    def write(self, fd, view):
        assert fd == self.FD
        self.writes += 1
        if self.write_eagain and self.writes % self.write_eagain == 0:
            self.write_errors += 1
            raise BlockingIOError(35, 'Resource temporarily unavailable')
        n = min(len(view), self.write_max)
        self.partial += n < len(view)
        if self.device:
            self.device.receive(bytes(view[:n]))
        return n

    def close(self, fd):
        pass

    def patch(self, case):
        fake_os = mock.Mock(read=self.read, write=self.write, close=self.close)
        fake_select = mock.Mock(select=self.select)
        for target, value in (('os', fake_os), ('select', fake_select)):
            patcher = mock.patch.object(transport, target, value)
            patcher.start()
            case.addCleanup(patcher.stop)
        port = transport.SerialPort.__new__(transport.SerialPort)
        port.fd = self.FD
        return port


@pytest.mark.gate
class SerialTransportTest(unittest.TestCase):
    def stage(self, tty, size):
        data = random.Random(128).randbytes(size)
        port = tty.patch(self)
        dev = xyf_host.Device(port, on_log=None)
        self.assertEqual(dev.stage(data), 0)
        self.assertTrue(tty.device.ended)
        self.assertEqual(bytes(tty.device.staged), data)
        return tty

    def test_stage_survives_eagain_after_a_ready_read(self):
        tty = self.stage(FakeTty(FakeDevice(), read_eagain=3), 300 * 1024)
        self.assertGreater(tty.read_errors, 100)

    def test_stage_survives_eagain_and_partial_writes(self):
        tty = self.stage(FakeTty(FakeDevice(), write_eagain=4, write_max=300), 300 * 1024)
        self.assertGreater(tty.write_errors, 100)
        self.assertGreater(tty.partial, 100)

    def test_stage_survives_both_at_once(self):
        self.stage(FakeTty(FakeDevice(), read_eagain=2, write_eagain=3, write_max=97), 64 * 1024)

    def test_read_keeps_its_timeout(self):
        port = FakeTty(None).patch(self)               # always ready, never data
        start = time.monotonic()
        self.assertEqual(port.read(0.05), b'')
        self.assertLess(time.monotonic() - start, 1.0)
        dev = xyf_host.Device(port, on_log=None)
        start = time.monotonic()
        with self.assertRaises(TimeoutError):
            dev.call(P.HELLO, timeout=0.05)
        self.assertLess(time.monotonic() - start, 1.0)

    def test_write_that_never_progresses_times_out(self):
        port = FakeTty(None, write_eagain=1).patch(self)
        with mock.patch.object(transport, 'WRITE_STALL', 0.05), self.assertRaises(TimeoutError):
            port.write(b'x' * 10)

    @unittest.skipUnless(hasattr(os, 'openpty'), 'needs a pty')
    def test_open_is_exclusive(self):
        """The port is opened with TIOCEXCL (macOS: no other process can open it until this one closes;
        a macOS pty ignores it, so only the request is checked here)."""
        master, slave = os.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        calls = []
        real = transport.fcntl.ioctl
        with mock.patch.object(transport.fcntl, 'ioctl', side_effect=lambda fd, op, *a: calls.append(op) or
                               real(fd, op, *a)):
            os.close(transport._open_tty(os.ttyname(slave)))
        self.assertEqual(calls, [transport.termios.TIOCEXCL])


@pytest.mark.gate
@unittest.skipUnless(hasattr(os, 'openpty'), 'needs a pty')
class SilentDeviceTest(unittest.TestCase):
    """Issue #118: a device that stops answering USB (a halted B) held the port's open in the kernel
    for about 60 s. Opening runs in a detached process, bounded; the last close never waits."""

    def setUp(self):
        self.master, slave = os.openpty()
        self.path = os.ttyname(slave)
        os.close(slave)                          # the port's only opener is SerialPort
        self.addCleanup(os.close, self.master)
        self.addCleanup(lambda: transport._opening.pop(self.path, mock.Mock()).close())

    def assertQuick(self, start, limit):
        self.assertLess(time.monotonic() - start, limit)

    def assertClosedByDetachedProcess(self, port):
        fd, start = port.fd, time.monotonic()
        port.close()
        port.close()
        self.assertQuick(start, 0.5)
        with self.assertRaises(OSError):
            os.fstat(fd)
        ready, _, _ = select.select([self.master], [], [], 2.0)    # the last close ends the pty's slave
        self.assertTrue(ready)

    def test_open_write_read_close(self):
        port = transport.SerialPort(self.path)
        port.write(b'hello')
        self.assertEqual(os.read(self.master, 16), b'hello')
        os.write(self.master, b'back')
        self.assertEqual(port.read(1.0), b'back')
        self.assertClosedByDetachedProcess(port)
        with self.assertRaises(FileNotFoundError):
            transport.SerialPort(self.path + '-gone')

    def test_open_that_hangs_times_out_and_is_not_started_twice(self):
        with mock.patch.object(transport, '_open_tty', lambda path: time.sleep(1.5)), \
                mock.patch.object(transport, '_opener', wraps=transport._opener) as opener:
            for _ in range(2):
                start = time.monotonic()
                with self.assertRaises(TimeoutError):
                    transport.SerialPort(self.path, timeout=0.05)
                self.assertQuick(start, 1.0)
        self.assertEqual(opener.call_count, 1)

    def test_a_late_open_reaches_the_next_port(self):
        real = transport._open_tty
        with mock.patch.object(transport, '_open_tty', lambda path: time.sleep(0.2) or real(path)):
            with self.assertRaises(TimeoutError):
                transport.SerialPort(self.path, timeout=0.05)
            port = transport.SerialPort(self.path, timeout=2.0)
        port.write(b'late')
        self.assertEqual(os.read(self.master, 16), b'late')
        self.assertClosedByDetachedProcess(port)

    def test_undrained_port_times_out_and_closes_at_once(self):
        port = transport.SerialPort(self.path)
        start = time.monotonic()
        with mock.patch.object(transport, 'WRITE_STALL', 0.05), self.assertRaises(TimeoutError):
            port.write(b'x' * (1 << 20))         # nobody reads the pty: its queue fills and stays full
        self.assertQuick(start, 1.0)
        self.assertClosedByDetachedProcess(port)

    def test_close_never_waits_for_the_kernel(self):
        port = transport.SerialPort(self.path)
        with mock.patch.object(transport, '_final_close', lambda fd, done: time.sleep(1.5)):
            start = time.monotonic()
            port.close()
        self.assertQuick(start, 0.5)

    def connect(self, timeout):
        start = time.monotonic()
        with mock.patch.object(xyf_host, 'SIM', None), mock.patch.object(xyf_host, 'log'), \
                mock.patch.object(xyf_host, 'find_port', return_value=self.path), \
                self.assertRaises(SystemExit):
            xyf_host.connect(timeout, hello_every=0.05)
        return time.monotonic() - start

    def test_connect_to_a_silent_port_keeps_its_timeout(self):
        self.assertLess(self.connect(0.05), 0.05 + 1.0)

    def test_connect_to_a_port_that_never_opens_keeps_its_timeout(self):
        with mock.patch.object(transport, '_open_tty', lambda path: time.sleep(3.0)), \
                mock.patch.object(transport, 'OPEN_LIMIT', 0.05):
            self.assertLess(self.connect(0.05), 0.05 + 0.05 + 1.0)


if __name__ == '__main__':
    unittest.main()
