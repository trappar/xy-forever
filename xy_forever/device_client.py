"""The device client every host tool shares: the connection to the XY Forever USB service (the OP-XY's CDC port, or
the simulator's TCP endpoint), the request and reply calls (Device) and the wait for the menu (connect). xyf-host
(xyf_host.py) and the developer package's `xyf` (xyf.py, #284) both use it, so it imports nothing beyond the
transport, the protocol and the device lock.

Protocol (docs/PROTOCOL.md): the HELLO reply's `cmds` lists the commands the device answers; Device refuses to send
any other. A device without `cmds` (pre-v1, build 153 and earlier) is assumed to answer every 1.0 command.
"""

import hashlib
import os
import socket
import struct
import sys
import time
import zlib

from xy_forever import bounds, device_lock, host_trust, transport
from xy_forever import usb_proto as P
from xy_forever.transport import SerialPort, TcpPort, device_ports, sim_address

# Test 034: the stock CDC receive ring holds 0x800 bytes and the device drains it
# 256 bytes per poll; a 4 KiB PUT_DATA frame overflowed it (no reply). One
# 1 KiB frame (+ 20 bytes framing) is in flight at a time.
PUT_CHUNK = 1024
READ_RANGE = 1024  # device read_range_max (Test 038: 4 KiB replies hung)
INSTALL_TIMEOUT = 900.0  # host bound for one INSTALL (v3 link sessions have no device cap)
COPY_RATE = 10000.0  # file.copy: the host waits its timeout plus 1 s per this many bytes
SLOTS = ('/yaffs2/xy-forever/state-a.txt', '/yaffs2/xy-forever/state-b.txt')


SIM = os.environ.get('XYF_SIM') or None  # HOST:PORT,CONTROL of the device simulator (--sim)


def sim_listening(address):
    """True when the simulator's USB endpoint accepts a connection (the device's port exists)."""
    try:
        socket.create_connection(sim_address(address)[:2], timeout=0.5).close()
        return True
    except OSError:
        return False


def find_port():
    if SIM:
        return 'sim:' + SIM if sim_listening(SIM) else None
    ports = device_ports()
    return ports[0] if ports else None


def open_port(port):
    return TcpPort(port[len('sim:') :]) if port.startswith('sim:') else SerialPort(port)


def print_log(ms, text):
    sys.stderr.write('[device %8.3f s] %s\n' % (ms / 1000.0, text))
    sys.stderr.flush()


class Unsupported(Exception):
    """The device's HELLO says it does not answer this command (nothing was sent)."""


class Device:
    def __init__(self, transport, on_log=print_log):
        self.t = transport
        self.parser = P.Parser()
        self.seq = 0
        self.on_log = on_log
        self.log = []  # (device ms, text) of every LOG frame received
        self.hello_info = None  # P.Hello of the last HELLO reply (None: not seen, nothing refused)
        self.on_ask = host_trust.say_ask  # #65: the device asks (waiting): said once per call, sent again
        # #230: the simulator answers at once, so a whole call to it (asks included) ends within this
        self.sim_call_s = bounds.SIM_CALL_S if isinstance(transport, TcpPort) else None

    def _log_frame(self, body):
        if len(body) < 8:
            return
        ms, text = struct.unpack_from('<I', body, 4)[0], body[8:].decode('ascii', 'replace')
        self.log.append((ms, text))
        if self.on_log:
            self.on_log(ms, text)

    def call(self, kind, payload=b'', timeout=5.0):
        end = time.monotonic() + (self.sim_call_s or 0)
        return host_trust.call(
            lambda: self.once(
                kind, payload, timeout if not self.sim_call_s else min(timeout, max(0.0, end - time.monotonic()))
            ),
            kind,
            self.on_ask,
        )

    def once(self, kind, payload=b'', timeout=5.0):
        info = self.hello_info
        if kind != P.HELLO and info is not None and not info.answers(kind):
            name = P.ids.COMMANDS[kind]['name'] if kind in P.ids.COMMANDS else '0x%02x' % kind
            raise Unsupported(
                'the device (%s) does not answer %s (0x%02x); update XY Forever' % (info.describe(), name, kind)
            )
        self.seq = (self.seq + 1) & 0xFF
        self.t.write(P.encode(kind, self.seq, payload))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for k, seq, body in self.parser.feed(self.t.read(0.2)):
                if k == P.LOG | P.REPLY and seq == 0:
                    self._log_frame(body)
                    continue
                if k == kind | P.REPLY and seq == self.seq:
                    if len(body) < 4:
                        raise OSError('short reply')
                    return struct.unpack_from('<I', body)[0], body[4:]
        raise TimeoutError('no reply to 0x%02x within %.1f s' % (kind, timeout))

    def hello(self):
        code, body = self.call(P.HELLO)
        text = body.decode('ascii', 'replace')
        if code == 0:
            self.hello_info = P.Hello(text)
        return code, text

    def read_file(self, path, offset=None, length=READ_RANGE):
        """Whole small file, or (offset given) up to length bytes from offset."""
        payload = path.encode() + b'\0' + (struct.pack('<II', offset, length) if offset is not None else b'')
        code, body = self.call(P.READ_FILE, payload, timeout=15.0)
        return code, body

    def read_all(self, path, limit=16 << 20, progress=None):
        """Any size: ranged reads until a short range."""
        data = bytearray()
        while len(data) < limit:
            code, body = self.read_file(path, len(data))
            if code:
                return code, bytes(data)
            data += body
            if progress:
                progress(len(data))
            if len(body) < READ_RANGE:
                return 0, bytes(data)
        return P.STATUS_NAMES.index('too_large'), bytes(data)

    def stage(self, data, progress=None):
        sha = hashlib.sha256(data).digest()
        code, _ = self.call(P.PUT_BEGIN, struct.pack('<II', len(data), zlib.crc32(data) & 0xFFFFFFFF) + sha)
        if code:
            return code
        for offset in range(0, len(data), PUT_CHUNK):
            chunk = data[offset : offset + PUT_CHUNK]
            code, body = self.call(P.PUT_DATA, struct.pack('<I', offset) + chunk)
            if code:
                return code
            if progress:
                progress(offset + len(chunk), len(data))
        code, _ = self.call(P.PUT_END, timeout=10.0)
        return code

    def set_default(self, entry_id):
        # b waits for a's write. Busy is answered before anything is done: right after a pack change its write window
        # is still settling (#288), so it is asked again for a while.
        from xy_forever import package_store

        code, body = package_store.unless_busy(
            self, lambda: self.call(P.SET_DEFAULT, entry_id.encode('ascii') + b'\0', timeout=75.0), every=0.25
        )
        sent_hash = struct.unpack_from('<I', body)[0] if len(body) >= 4 else 0
        return code, sent_hash

    def library_boot(self, entry_id):
        """library.boot (1.7): boot entry_id once; ok means the device leaves the menu and USB goes away."""
        raw = entry_id.encode('ascii')
        code, _ = self.call(
            P.ids.LIBRARY_BOOT,
            raw + b'\0' + hashlib.sha256(raw).digest(),
            timeout=P.ids.COMMANDS[P.ids.LIBRARY_BOOT]['timeout_ms'] / 1000.0,
        )
        return code

    def file_write(self, path, sha, timeout):
        code, body = self.call(P.FILE_WRITE, path.encode('utf-8') + b'\0' + sha, timeout=timeout)
        written, back = struct.unpack_from('<II', body) if len(body) >= 8 else (0, 0)
        return code, written, back

    def list_page(self, path, skip=0):
        """One file.list page: (status, entries [(kind, size, name bytes)], more)."""
        code, body = self.call(
            P.LIST_DIR,
            path.encode('utf-8') + b'\0' + struct.pack('<I', skip),
            timeout=P.ids.COMMANDS[P.LIST_DIR]['timeout_ms'] / 1000.0,
        )
        if code:
            return code, [], 0
        if len(body) < 8:
            raise OSError('file.list: short reply for %s' % path)
        count, more = struct.unpack_from('<II', body)
        entries = parse_entries(body[8:])
        if len(entries) != count:
            raise OSError('file.list: %d entries announced, %d decoded' % (count, len(entries)))
        return code, entries, more

    def list_dir(self, path):
        """Every entry of a folder, page by page: (status, entries)."""
        found = []
        while True:
            code, entries, more = self.list_page(path, len(found))
            if code:
                return code, found
            found += entries
            if not more:
                return 0, found

    def change(self, kind, path):
        """file.delete, file.rmdir or file.mkdir, bound to the path's SHA-256: (status, error number)."""
        raw = path.encode('utf-8')
        code, body = self.call(
            kind, raw + b'\0' + hashlib.sha256(raw).digest(), timeout=P.ids.COMMANDS[kind]['timeout_ms'] / 1000.0
        )
        return code, struct.unpack_from('<I', body)[0] if len(body) >= 4 else 0

    def rename(self, source, target):
        """file.rename, bound to the SHA-256 of source NUL target: (status, error number)."""
        pair = source.encode('utf-8') + b'\0' + target.encode('utf-8')
        code, body = self.call(
            P.RENAME_FILE,
            pair + b'\0' + hashlib.sha256(pair).digest(),
            timeout=P.ids.COMMANDS[P.RENAME_FILE]['timeout_ms'] / 1000.0,
        )
        return code, struct.unpack_from('<I', body)[0] if len(body) >= 4 else 0

    def copy(self, source, target, size=0):
        """file.copy, bound to the SHA-256 of source NUL target: (status, bytes, error number, partial)."""
        pair = source.encode('utf-8') + b'\0' + target.encode('utf-8')
        code, body = self.call(
            P.COPY_FILE,
            pair + b'\0' + hashlib.sha256(pair).digest(),
            timeout=P.ids.COMMANDS[P.COPY_FILE]['timeout_ms'] / 1000.0 + size / COPY_RATE,
        )
        return (code,) + (struct.unpack_from('<III', body) if len(body) >= 12 else (0, 0, 0))

    def install(self, sha):
        code, body = self.call(P.INSTALL, sha, timeout=INSTALL_TIMEOUT)
        written, partition, sectors = struct.unpack_from('<III', body) if len(body) >= 12 else (0, 0, 0)
        return code, written, partition, sectors

    def uninstall(self, sha):
        code, body = self.call(P.UNINSTALL, sha, timeout=INSTALL_TIMEOUT)
        written, partition, sectors = struct.unpack_from('<III', body) if len(body) >= 12 else (0, 0, 0)
        return code, written, partition, sectors


def parse_entries(data):
    """file.list entries: [(kind, size, name bytes)] ({u8 kind, u32 size, u8 name bytes, name} each)."""
    out, at = [], 0
    while at + 6 <= len(data):
        kind, size, n = struct.unpack_from('<BIB', data, at)
        if at + 6 + n > len(data):
            raise OSError('file.list: an entry runs past the reply')
        out.append((kind, size, bytes(data[at + 6 : at + 6 + n])))
        at += 6 + n
    if at != len(data):
        raise OSError('file.list: %d bytes after the last entry' % (len(data) - at))
    return out


def parse_slot(data):
    """Host copy of xyf::state::parse_slot: (seq, fields) or None."""
    if len(data) < 16 or len(data) > 4096 or not data.endswith(b'\n'):
        return None
    line = len(data) - 15
    if data[line : line + 6] != b'crc32=' or (line and data[line - 1 : line] != b'\n'):
        return None
    try:
        expected = int(data[line + 6 : line + 14].decode('ascii'), 16)
    except ValueError:
        return None
    if (
        data[line + 6 : line + 14] != data[line + 6 : line + 14].lower()
        or zlib.crc32(data[:line]) & 0xFFFFFFFF != expected
    ):
        return None
    fields = {}
    for raw in data[:line].decode('utf-8', 'replace').splitlines():
        raw = raw.strip()
        if raw and not raw.startswith('#') and '=' in raw:
            k, v = raw.split('=', 1)
            fields[k.strip()] = v.strip()
    if not fields.get('seq', '').isdigit():
        return None
    return int(fields['seq']), fields


def current_state(slot_bytes):
    """Newest valid slot from {path: bytes or None}: (path, seq, fields) or None."""
    best = None
    for path, data in slot_bytes.items():
        parsed = parse_slot(data) if data else None
        if parsed and (best is None or parsed[0] > best[1]):
            best = (path, parsed[0], parsed[1])
    return best


T0 = time.monotonic()


def log(message):
    """Timestamped progress on stderr (device-test evidence: when the port appeared, what was sent)."""
    sys.stderr.write('[%7.2f s] %s\n' % (time.monotonic() - T0, message))
    sys.stderr.flush()


def connect(timeout, hello_every=0.5, hello_window=8.0, log=log, waiting=None):
    """Waits for a /dev/cu.usbmodem* port and sends HELLO every hello_every seconds on the same open
    port (the device answers only during its ~6 s service window). Every failed port is closed; the
    whole wait never exceeds timeout. The device lock first (xy_forever/device_lock.py; not for the simulator).
    log: its progress lines (stderr); waiting(): called once, the first time no port is there or a port does not
    answer HELLO (the OP-XY is off or in its OS: the XY Forever menu is not running)."""
    deadline = time.monotonic() + (min(timeout, bounds.SIM_CONNECT_S) if SIM else timeout)
    if not SIM:
        device_lock.hold(timeout)
    seen = None

    def wait_once():
        nonlocal waiting
        if waiting:
            waiting()
            waiting = None

    while time.monotonic() < deadline:
        port = find_port()
        if port != seen:
            log('port %s' % (port or 'gone'))
            seen = port
        if not port:
            wait_once()
            pause(0.1)
            continue
        try:
            transport = open_port(port)
        except OSError as error:
            log('open %s failed: %s' % (port, error))
            pause(0.3)
            continue
        dev, sent, received = Device(transport), 0, 0
        window = min(deadline, time.monotonic() + hello_window)
        try:
            while time.monotonic() < window:
                dev.seq = (dev.seq + 1) & 0xFF
                transport.write(P.encode(P.HELLO, dev.seq, b''))
                sent += 1
                until = time.monotonic() + hello_every
                while time.monotonic() < until:
                    data = transport.read(0.05)
                    received += len(data)
                    for k, _, body in dev.parser.feed(data):
                        if k == P.HELLO | P.REPLY and len(body) >= 4:
                            code = struct.unpack_from('<I', body)[0]
                            log('HELLO reply after %d sent, %d bytes received' % (sent, received))
                            if code == 0:
                                text = body[4:].decode('ascii', 'replace')
                                dev.hello_info = P.Hello(text)
                                return dev, port, text
        except (OSError, TimeoutError) as error:
            log('%s: %s' % (port, error))
        log('no HELLO reply on %s: %d sent, %d bytes received' % (port, sent, received))
        wait_once()
        try:
            transport.close()
        except OSError:
            pass
        pause(0.3)
    raise SystemExit(
        'XY Forever USB service not found within %g s (USB attached; to reach the menu, power on holding PLAY)'
        % timeout
    )


def pause(seconds):
    """The host waits on the device: on the simulator its clock moves on instead (transport.sim_pause); a simulator
    that is not there ends the command, as no port would."""
    if SIM:
        try:
            transport.sim_pause(SIM, seconds)
        except ConnectionRefusedError as error:
            raise SystemExit('%s: is the simulator running (mise run sim)? --sim %s' % (error, SIM)) from error
    else:
        time.sleep(seconds)
