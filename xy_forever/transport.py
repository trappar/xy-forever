"""xyf_host.py's byte transports: the OP-XY's CDC tty (SerialPort) and the simulator's TCP port (TcpPort).

Both are raw byte streams: write(data) sends all of data or raises TimeoutError; read(timeout) returns
what arrived within timeout, or b'' (the caller's own deadline decides when to give up).
"""
import fcntl
import glob
import os
import select
import socket
import termios
import threading
import time

from xy_forever import device_lock, no_device

WRITE_STALL = 2.0      # seconds a write may make no progress before it is a timeout
OPEN_LIMIT = 3.0       # seconds SerialPort waits for the port to open before it is a timeout
_opening = {}          # path: the socket of an open that outlived its wait (the next SerialPort waits on it)


def device_ports():
    """The OP-XY's CDC ports, sorted; a test run is refused before it looks (xy_forever/no_device.py). Takes the
    device lock first (xy_forever/device_lock.py)."""
    no_device.refuse('the serial port (/dev/cu.usbmodem*)')
    device_lock.hold()
    return sorted(glob.glob('/dev/cu.usbmodem*'))


def _detached(work, keep):
    """Runs work() in a detached grandchild (own session, stdio on /dev/null, every fd but keep closed):
    nothing waits for it, so a system call the kernel holds there holds no caller (issue #118)."""
    pid = os.fork()
    if pid:
        os.waitpid(pid, 0)                       # the middle child exits at once: no zombie is left
        return
    try:
        os.setsid()
        if os.fork() == 0:
            null = os.open(os.devnull, os.O_RDWR)
            for std in (0, 1, 2):
                os.dup2(null, std)
            for fd in {int(name) for name in os.listdir('/dev/fd')} - set(keep) - {0, 1, 2}:
                try:
                    os.close(fd)
                except OSError:
                    pass                         # listdir's own, already closed
            work()
    finally:
        os._exit(0)


def _open_tty(path):
    """The port's fd, configured: raw, exclusive (TIOCEXCL), queues flushed."""
    no_device.refuse_tty(path)
    fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    try:
        fcntl.ioctl(fd, termios.TIOCEXCL)
        attrs = termios.tcgetattr(fd)
        attrs[0] = 0
        attrs[1] = 0
        attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
        attrs[3] = 0
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
        termios.tcflush(fd, termios.TCIOFLUSH)
    except BaseException:
        os.close(fd)
        raise
    return fd


def _opener(path):
    """A socket that receives path's fd (or 'errno text') from a detached _open_tty."""
    ours, theirs = socket.socketpair()

    def work():
        try:
            fd = _open_tty(path)
        except OSError as error:
            theirs.sendall(('%d %s' % (error.errno or 0, error.strerror or error)).encode())
        else:
            socket.send_fds(theirs, [b'ok'], [fd])
    try:
        _detached(work, (theirs.fileno(),))
    finally:
        theirs.close()
    return ours


def _final_close(fd, parent_done):
    os.read(parent_done, 1)                      # EOF: the caller has dropped its copy of fd
    os.close(fd)


class SerialPort:
    """Raw termios transport on a CDC tty, opened for this process only.

    macOS does not lock a /dev/cu.* port: a second process (scripts/keepalive.sh's status call every
    2 s) could open it during a transfer, flush its input on open, send its own HELLO and take replies.
    Both processes then wake on the same input and one finds nothing to read (EAGAIN). TIOCEXCL makes
    any other open fail with EBUSY until this one closes (connect() in xyf_host.py retries an open).

    A device that stops answering USB (a halted B) can hold the open, its settings and the last close
    inside the kernel for about 60 s, whatever O_NONBLOCK says (issue #118). So the open runs in a
    detached process that passes the fd back, bounded by timeout; an open that outlives it is waited on
    by the next SerialPort of that path, never started twice. The last close runs in a detached process.
    """

    def __init__(self, path, timeout=None):
        self.fd = None
        no_device.refuse_tty(path)
        if not no_device.PTY.match(path):
            device_lock.hold()
        timeout = OPEN_LIMIT if timeout is None else timeout
        sock = _opening.pop(path, None) or _opener(path)
        ready, _, _ = select.select([sock], [], [], timeout)
        if not ready:
            _opening[path] = sock
            raise TimeoutError('%s did not open within %g s (the device is not answering USB)' % (path, timeout))
        with sock:
            message, fds, _, _ = socket.recv_fds(sock, 256, 1)
        if not fds:
            code, _, text = message.decode('ascii', 'replace').partition(' ')
            raise OSError(int(code) if code.isdigit() else 0, text or 'the port opener stopped')
        self.fd = fds[0]

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
        """Never waits for the device: a detached process makes the last close (the kernel may hold it)."""
        fd, self.fd = self.fd, None              # idempotent (scripts/device_pass.py closes every port)
        if fd is None:
            return
        done, hold = os.pipe()
        try:
            _detached(lambda: _final_close(fd, done), (fd, done))
        finally:
            os.close(done)
            os.close(fd)
            os.close(hold)


def sim_address(address):
    host, _, port = address.rpartition(':')
    if host not in ('127.0.0.1', 'localhost', '::1') or not port.isdigit():
        raise ValueError('--sim needs a loopback HOST:PORT, e.g. 127.0.0.1:7341 (got %r)' % address)
    return host, int(port)


_SIM_CONTROL = {}           # (host, control port) -> [socket, lock]: one kept connection each (no TIME_WAIT per pause)


def sim_pause(address, seconds):
    """A host's pause against the simulator: its device clock moves on that long (control "advance MS" on port + 1,
    answered once it is there), in no host time. The device sees the pause as on the OP-XY (sim/clock.cpp: device
    time stands still while nothing is due). One kept control connection per simulator: a pause each few
    milliseconds of host time would otherwise use up the local ports."""
    host, port = sim_address(address)
    entry = _SIM_CONTROL.setdefault((host, port + 1), [None, threading.Lock()])
    line = b'advance %d\n' % max(1, int(round(seconds * 1000)))
    with entry[1]:
        for attempt in (0, 1):
            try:
                if entry[0] is None:
                    entry[0] = socket.create_connection((host, port + 1), timeout=10.0)
                entry[0].sendall(line)
                reply = b''
                while not reply.endswith(b'\n'):
                    chunk = entry[0].recv(256)
                    if not chunk:
                        raise OSError('the simulator closed its control port')
                    reply += chunk
                return
            except OSError as error:
                if entry[0] is not None:
                    entry[0].close()
                entry[0] = None
                if attempt or isinstance(error, socket.timeout):     # sent: never advance twice
                    raise


def pause(port, seconds):
    """The host waits seconds on the device: time.sleep, or on the simulator (TcpPort) its clock moves on."""
    getattr(port, 'pause', time.sleep)(seconds)


class TcpPort:
    """The device simulator's USB endpoint (python3 -m xy_forever.sim): the same raw byte stream
    on a local TCP port. Only loopback addresses: the simulator never listens elsewhere."""

    def __init__(self, address):
        host, port = sim_address(address)
        self.address = '%s:%d' % (host, port)
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

    def pause(self, seconds):
        sim_pause(self.address, seconds)

    def close(self):
        if self.fd is not None:
            self.sock.close()
            self.fd = None
