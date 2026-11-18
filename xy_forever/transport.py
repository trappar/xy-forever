"""xyf_host.py's byte transports: the OP-XY's CDC tty (SerialPort) and the simulator's TCP port (TcpPort).

Both are raw byte streams: write(data) sends all of data or raises TimeoutError; read(timeout) returns
what arrived within timeout, or b'' (the caller's own deadline decides when to give up).
"""
import fcntl
import os
import select
import socket
import termios
import time

WRITE_STALL = 2.0      # seconds a write may make no progress before it is a timeout


class SerialPort:
    """Raw termios transport on a CDC tty, opened for this process only.

    macOS does not lock a /dev/cu.* port: a second process (scripts/keepalive.sh's status call every
    2 s) could open it during a transfer, flush its input on open, send its own HELLO and take replies.
    Both processes then wake on the same input and one finds nothing to read (EAGAIN). TIOCEXCL makes
    any other open fail with EBUSY until this one closes (connect() in xyf_host.py retries an open).
    """

    def __init__(self, path):
        self.fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        try:
            fcntl.ioctl(self.fd, termios.TIOCEXCL)
            attrs = termios.tcgetattr(self.fd)
            attrs[0] = 0
            attrs[1] = 0
            attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
            attrs[3] = 0
            termios.tcsetattr(self.fd, termios.TCSANOW, attrs)
            termios.tcflush(self.fd, termios.TCIOFLUSH)
        except BaseException:
            os.close(self.fd)
            self.fd = None
            raise

    def write(self, data):
        """All of data, through partial writes and EAGAIN (the tty's output queue full)."""
        view = memoryview(data)
        stall = time.monotonic() + WRITE_STALL
        while view:
            left = stall - time.monotonic()
            _, ready, _ = select.select([], [self.fd], [], max(0.0, left))
            if not ready:
                raise TimeoutError('write timeout')
            try:
                n = os.write(self.fd, view)
            except (BlockingIOError, InterruptedError):
                n = 0
            if n:
                view = view[n:]
                stall = time.monotonic() + WRITE_STALL
            elif left <= 0:
                raise TimeoutError('write timeout')

    def read(self, timeout):
        """Bytes that arrived within timeout, or b''. A ready poll followed by EAGAIN (the input went
        between the poll and the read) is no data yet: it waits again until timeout."""
        deadline = time.monotonic() + timeout
        while True:
            left = deadline - time.monotonic()
            ready, _, _ = select.select([self.fd], [], [], max(0.0, left))
            if not ready:
                return b''
            try:
                return os.read(self.fd, 65536)
            except (BlockingIOError, InterruptedError):
                if left <= 0:
                    return b''

    def close(self):
        if self.fd is not None:                  # idempotent (scripts/device_pass.py closes every port)
            os.close(self.fd)
            self.fd = None


def sim_address(address):
    host, _, port = address.rpartition(':')
    if host not in ('127.0.0.1', 'localhost', '::1') or not port.isdigit():
        raise ValueError('--sim needs a loopback HOST:PORT, e.g. 127.0.0.1:7341 (got %r)' % address)
    return host, int(port)


class TcpPort:
    """The device simulator's USB endpoint (python3 -m xy_forever.sim): the same raw byte stream
    on a local TCP port. Only loopback addresses: the simulator never listens elsewhere."""

    def __init__(self, address):
        host, port = sim_address(address)
        self.sock = socket.create_connection((host, port), timeout=2.0)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.fd = self.sock.fileno()

    def write(self, data):
        _, ready, _ = select.select([], [self.sock], [], 2.0)
        if not ready:
            raise TimeoutError('write timeout')
        self.sock.sendall(data)

    def read(self, timeout):
        ready, _, _ = select.select([self.sock], [], [], timeout)
        if not ready:
            return b''
        data = self.sock.recv(65536)
        if not data:
            raise OSError('the simulator closed the connection (device restart or USB off)')
        return data

    def close(self):
        if self.fd is not None:
            self.sock.close()
            self.fd = None
