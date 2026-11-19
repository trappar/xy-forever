"""XY Forever host tool: talk to the XY Forever USB service (CDC serial).

Usage (stdlib only; macOS /dev/cu.usbmodem*):
  python3 xyf_host.py wait                     wait for the service, send HELLO (prints protocol and build)
  python3 xyf_host.py status                   protocol, version, image and decoded result blocks
  python3 xyf_host.py read /yaffs2/firmware.version [--out FILE]
  python3 xyf_host.py read /yaffs2/xy-forever/log/prod-069-b.bin --all --out FILE   (any size, 4 KiB ranges)
  python3 xyf_host.py install BUILD_DIR        DRY RUN: checks and prints the plan
  python3 xyf_host.py install BUILD_DIR --yes-install
  python3 xyf_host.py reboot --yes-reboot [--to service|os|menu]
  python3 xyf_host.py set-default ID           DRY RUN: prints the plan
  python3 xyf_host.py set-default ID --yes-write   processor a writes the next state slot
  python3 xyf_host.py put-file LOCAL /yaffs2/xy-forever/PATH   DRY RUN: prints the plan
  python3 xyf_host.py put-file LOCAL /yaffs2/xy-forever/PATH --yes-write   processor a writes it
  python3 xyf_host.py ls /yaffs2/xy-forever [--json]   list a folder: type, size, name
  python3 xyf_host.py get /yaffs2/PATH LOCAL   copy a file to the computer (any size; size and ranges checked)
  python3 xyf_host.py put LOCAL /yaffs2/xy-forever/PATH   copy a file to the OP-XY (up to the staging limit,
                                               read back and compared; --no-verify skips the read-back)
  python3 xyf_host.py rm /yaffs2/PATH          delete a file (or an empty folder with --dir)
  python3 xyf_host.py mkdir /yaffs2/PATH       create a folder (its parent must exist)
  python3 xyf_host.py library-add ID OS_LDR --name NAME --version VER [--content DIR]   DRY RUN: plan
  python3 xyf_host.py library-add ID OS_LDR --name NAME --version VER --content DIR --yes-write
                                               firmware/ID/os.ldr + info.txt, and from DIR (the official
                                               content) content.sha256 + firmware.version (reboots between)
  python3 xyf_host.py screenshot --out FILE.png   the display (read-only framebuffer view)
  python3 xyf_host.py tfw-check /yaffs2/PATH   is a .tfw (or a cut) on the device official, and do the
                                               device's own OTP keys open it? JSON; writes nothing
  python3 xyf_host.py key NAME [--hold]        menu over USB: minus plus play stop shift track1..track8
  python3 xyf_host.py continue                 leave the service, normal boot
  python3 xyf_host.py boot ID --yes-boot       boot library entry ID once (leaves XY Forever)
  python3 xyf_host.py continue --dry-run       the whole boot path without starting the OS, then back
  python3 xyf_host.py hold on|off              stay in the service across reboots
  python3 xyf_host.py uninstall STOCK_OS_LDR   DRY RUN: only the exact stock 1.2.1 os.ldr
  python3 xyf_host.py uninstall STOCK_OS_LDR --yes-uninstall
  python3 xyf_host.py mods dev PKG_DIR [--with ID...] [--base ID] [--boot | --watch]
                                               mod developers: push only changed files (SHA-256), build
                                               the mod on the device, make it the default (content
                                               installed), optionally boot; each phase timed
  python3 xyf_host.py mods dev --restore       the default from before the first dev run, again
  python3 xyf_host.py --sim 127.0.0.1:7341 status   the device simulator instead of the USB port
                                               (or XYF_SIM=127.0.0.1:7341; same commands, same checks)

Safety (docs/SAFETY.md, binding): install refuses anything that
is not a verified XY Forever build (build manifest + verification passed,
candidate SHA match, LDR structure, cold descriptor), stages it with size, CRC
and SHA-256, and confirms the install with the same SHA-256 as a token. The
device then writes only through the stock commit-last A/B writer and reads the
partition back. Default is a dry run.

Protocol (docs/PROTOCOL.md): the HELLO reply's `cmds` lists the commands the device
answers; the tool refuses to send any other. A device without `cmds` (pre-v1, build
153 and earlier) is assumed to answer every 1.0 command.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import struct
import sys
import time
import zlib

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from xy_forever import depth, host_write, library_add, usb_proto as P
from xy_forever.library_add import ID_RE
from xy_forever.host_install import DESCRIPTOR, MIN_IMAGE, IMAGE_CAPACITY, USB_MODES, load_build, load_stock
from xy_forever.ldr import MAX_IMAGE, inspect_ldr
from xy_forever.transport import SerialPort, TcpPort, device_ports, sim_address

# Test 034: the stock CDC receive ring holds 0x800 bytes and the device drains it
# 256 bytes per poll; a 4 KiB PUT_DATA frame overflowed it (no reply). One
# 1 KiB frame (+ 20 bytes framing) is in flight at a time.
PUT_CHUNK = 1024
READ_RANGE = 1024              # device read_range_max (Test 038: 4 KiB replies hung)
INSTALL_TIMEOUT = 900.0         # host bound for one INSTALL (v3 link sessions have no device cap)
COPY_RATE = 10000.0             # file.copy: the host waits its timeout plus 1 s per this many bytes
MTP_TEST_TIMEOUT = 60.0         # host bound for one MTP_TEST, plus its hold time
# Test 049 put-file: under /yaffs2/xy-forever/, plain names only (the device re-checks).
WRITE_PATH_RE = re.compile(r'^/yaffs2/xy-forever(/[A-Za-z0-9._-]{1,64}){1,4}$')
# Test 058: key numbers (xyf_menu.hpp / key_ui.cpp): KPlay 51, KStop 52, -/+ 53/54, shift 55, track1 6.
KEYS = dict(minus=53, plus=54, play=51, stop=52, shift=55, **{'track%d' % (i + 1): 6 + i for i in range(8)})
# put/rm/mkdir write only here (the device's write area: target/storage_server.cpp write_root).
AREA_TEXT = 'under /yaffs2/, never bootloader.dev or upgrade/'
WRITE_ROOTS = ('/yaffs2/',)       # since 1.13: all of /yaffs2 but the two below
SLOTS = ('/yaffs2/xy-forever/state-a.txt', '/yaffs2/xy-forever/state-b.txt')
SCREEN_BYTES, SCREEN_W, SCREEN_H, SCREEN_STRIDE = 0x3c000, 480, 216, 256   # ui_gfx.hpp


SIM = os.environ.get('XYF_SIM') or None
# tfw.check reasons (protocol/v1.json; native/tfw_check.hpp Reason).
TFW_REASONS = ('pass', 'read', 'not_package', 'key_index', 'no_otp', 'public_key', 'signature', 'decrypt', 'directory')
TFW_CHECK_TIMEOUT = 60.0        # HOST:PORT of the device simulator (--sim)


def sim_listening(address):
    """True when the simulator's USB endpoint accepts a connection (the device's port exists)."""
    try:
        socket.create_connection(sim_address(address), timeout=0.5).close()
        return True
    except OSError:
        return False


def find_port():
    if SIM:
        return 'sim:' + SIM if sim_listening(SIM) else None
    ports = device_ports()
    return ports[0] if ports else None


def open_port(port):
    return TcpPort(port[len('sim:'):]) if port.startswith('sim:') else SerialPort(port)


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
        self.log = []                # (device ms, text) of every LOG frame received
        self.hello_info = None       # P.Hello of the last HELLO reply (None: not seen, nothing refused)

    def _log_frame(self, body):
        if len(body) < 8:
            return
        ms, text = struct.unpack_from('<I', body, 4)[0], body[8:].decode('ascii', 'replace')
        self.log.append((ms, text))
        if self.on_log:
            self.on_log(ms, text)

    def call(self, kind, payload=b'', timeout=5.0):
        info = self.hello_info
        if kind != P.HELLO and info is not None and not info.answers(kind):
            name = P.ids.COMMANDS[kind]['name'] if kind in P.ids.COMMANDS else '0x%02x' % kind
            raise Unsupported('the device (%s) does not answer %s (0x%02x); update XY Forever' %
                              (info.describe(), name, kind))
        self.seq = (self.seq + 1) & 0xff
        self.t.write(P.encode(kind, self.seq, payload))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for k, seq, body in self.parser.feed(self.t.read(0.2)):
                if k == P.LOG | P.REPLY and seq == 0:
                    self._log_frame(body)
                    continue
                if k == kind | P.REPLY and seq == self.seq:
                    if len(body) < 4:
                        raise IOError('short reply')
                    return struct.unpack_from('<I', body)[0], body[4:]
        raise TimeoutError('no reply to 0x%02x' % kind)

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
        code, _ = self.call(P.PUT_BEGIN, struct.pack('<II', len(data), zlib.crc32(data) & 0xffffffff) + sha)
        if code:
            return code
        for offset in range(0, len(data), PUT_CHUNK):
            chunk = data[offset:offset + PUT_CHUNK]
            code, body = self.call(P.PUT_DATA, struct.pack('<I', offset) + chunk)
            if code:
                return code
            if progress:
                progress(offset + len(chunk), len(data))
        code, _ = self.call(P.PUT_END, timeout=10.0)
        return code

    def set_default(self, entry_id):
        code, body = self.call(P.SET_DEFAULT, entry_id.encode('ascii') + b'\0', timeout=75.0)   # b waits for a's write
        sent_hash = struct.unpack_from('<I', body)[0] if len(body) >= 4 else 0
        return code, sent_hash

    def library_boot(self, entry_id):
        """library.boot (1.7): boot entry_id once; ok means the device leaves the menu and USB goes away."""
        raw = entry_id.encode('ascii')
        code, _ = self.call(P.ids.LIBRARY_BOOT, raw + b'\0' + hashlib.sha256(raw).digest(),
                            timeout=P.ids.COMMANDS[P.ids.LIBRARY_BOOT]['timeout_ms'] / 1000.0)
        return code

    def file_write(self, path, sha, timeout):
        code, body = self.call(P.FILE_WRITE, path.encode('utf-8') + b'\0' + sha, timeout=timeout)
        written, back = struct.unpack_from('<II', body) if len(body) >= 8 else (0, 0)
        return code, written, back

    def list_page(self, path, skip=0):
        """One file.list page: (status, entries [(kind, size, name bytes)], more)."""
        code, body = self.call(P.LIST_DIR, path.encode('utf-8') + b'\0' + struct.pack('<I', skip),
                               timeout=P.ids.COMMANDS[P.LIST_DIR]['timeout_ms'] / 1000.0)
        if code or len(body) < 8:
            return code, [], 0
        count, more = struct.unpack_from('<II', body)
        entries = parse_entries(body[8:])
        if len(entries) != count:
            raise IOError('file.list: %d entries announced, %d decoded' % (count, len(entries)))
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
        code, body = self.call(kind, raw + b'\0' + hashlib.sha256(raw).digest(),
                               timeout=P.ids.COMMANDS[kind]['timeout_ms'] / 1000.0)
        return code, struct.unpack_from('<I', body)[0] if len(body) >= 4 else 0

    def rename(self, source, target):
        """file.rename, bound to the SHA-256 of source NUL target: (status, error number)."""
        pair = source.encode('utf-8') + b'\0' + target.encode('utf-8')
        code, body = self.call(P.RENAME_FILE, pair + b'\0' + hashlib.sha256(pair).digest(),
                               timeout=P.ids.COMMANDS[P.RENAME_FILE]['timeout_ms'] / 1000.0)
        return code, struct.unpack_from('<I', body)[0] if len(body) >= 4 else 0

    def copy(self, source, target, size=0):
        """file.copy, bound to the SHA-256 of source NUL target: (status, bytes, error number, partial)."""
        pair = source.encode('utf-8') + b'\0' + target.encode('utf-8')
        code, body = self.call(P.COPY_FILE, pair + b'\0' + hashlib.sha256(pair).digest(),
                               timeout=P.ids.COMMANDS[P.COPY_FILE]['timeout_ms'] / 1000.0 + size / COPY_RATE)
        return (code,) + (struct.unpack_from('<III', body) if len(body) >= 12 else (0, 0, 0))

    def install(self, sha):
        code, body = self.call(P.INSTALL, sha, timeout=INSTALL_TIMEOUT)
        written, partition, sectors = struct.unpack_from('<III', body) if len(body) >= 12 else (0, 0, 0)
        return code, written, partition, sectors

    def uninstall(self, sha):
        code, body = self.call(P.UNINSTALL, sha, timeout=INSTALL_TIMEOUT)
        written, partition, sectors = struct.unpack_from('<III', body) if len(body) >= 12 else (0, 0, 0)
        return code, written, partition, sectors


KINDS = ('file', 'dir', 'other')


def parse_entries(data):
    """file.list entries: [(kind, size, name bytes)] ({u8 kind, u32 size, u8 name bytes, name} each)."""
    out, at = [], 0
    while at + 6 <= len(data):
        kind, size, n = struct.unpack_from('<BIB', data, at)
        if at + 6 + n > len(data):
            raise IOError('file.list: an entry runs past the reply')
        out.append((kind, size, bytes(data[at + 6:at + 6 + n])))
        at += 6 + n
    if at != len(data):
        raise IOError('file.list: %d bytes after the last entry' % (len(data) - at))
    return out


def entry_dict(entry):
    kind, size, name = entry
    return dict(name=name.decode('utf-8', 'replace'), type=KINDS[kind] if kind < len(KINDS) else 'kind_%d' % kind,
                size=size)


def device_path(path):
    """An absolute device path under /yaffs2 (the device checks it again), or SystemExit."""
    if path != '/yaffs2' and not path.startswith('/yaffs2/'):
        raise SystemExit('refused: %r is not an absolute device path under /yaffs2' % path)
    return path


def in_write_area(path):
    """The device's write area (since 1.13): below /yaffs2/, no empty, '.' or '..' part, never
    /yaffs2/bootloader.dev nor /yaffs2/upgrade or below it (ASCII case ignored). The device checks again."""
    root = next((r for r in WRITE_ROOTS if path.startswith(r)), None)
    rest = path[len(root):].split('/') if root else []
    if not rest or not all(p and p not in ('.', '..') for p in rest):
        return False
    first = rest[0].lower()
    return first != 'upgrade' and not (len(rest) == 1 and first == 'bootloader.dev')


def split_path(path):
    parent, _, name = path.rstrip('/').rpartition('/')
    return parent or '/yaffs2', name


def sample_offsets(size):
    """get's second read: the first, the last and two middle READ_RANGE ranges of a file."""
    last = max(0, (size - 1) // READ_RANGE)
    return sorted({0, last, last // 3, 2 * last // 3})


def parse_slot(data):
    """Host copy of xyf::state::parse_slot: (seq, fields) or None."""
    if len(data) < 16 or len(data) > 4096 or not data.endswith(b'\n'):
        return None
    line = len(data) - 15
    if data[line:line + 6] != b'crc32=' or (line and data[line - 1:line] != b'\n'):
        return None
    try:
        expected = int(data[line + 6:line + 14].decode('ascii'), 16)
    except ValueError:
        return None
    if data[line + 6:line + 14] != data[line + 6:line + 14].lower() or zlib.crc32(data[:line]) & 0xffffffff != expected:
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


def connect(timeout, hello_every=0.5, hello_window=8.0):
    """Waits for a /dev/cu.usbmodem* port and sends HELLO every hello_every seconds on the same open
    port (the device answers only during its ~6 s service window). Every failed port is closed; the
    whole wait never exceeds timeout."""
    deadline = time.monotonic() + timeout
    seen = None
    while time.monotonic() < deadline:
        port = find_port()
        if port != seen:
            log('port %s' % (port or 'gone'))
            seen = port
        if not port:
            time.sleep(0.1)
            continue
        try:
            transport = open_port(port)
        except OSError as error:
            log('open %s failed: %s' % (port, error))
            time.sleep(0.3)
            continue
        dev, sent, received = Device(transport), 0, 0
        window = min(deadline, time.monotonic() + hello_window)
        try:
            while time.monotonic() < window:
                dev.seq = (dev.seq + 1) & 0xff
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
        except (OSError, TimeoutError, IOError) as error:
            log('%s: %s' % (port, error))
        log('no HELLO reply on %s: %d sent, %d bytes received' % (port, sent, received))
        try:
            transport.close()
        except OSError:
            pass
        time.sleep(0.3)
    raise SystemExit('XY Forever USB service not found (power on with USB attached, no keys held)')


def stage_progress(done, total):
    if done == total or done % 0x10000 < PUT_CHUNK:
        print('\rstaged %d/%d' % (done, total), end='', flush=True)


def sent_progress(done, total):
    if done == total or done % 0x10000 < PUT_CHUNK:
        print('\rsent %d/%d' % (done, total), end='\n' if done == total else '', flush=True)


def fresh(dev, timeout):
    """REBOOT (proven path) and reconnect: a fresh boot has no storage use yet, which
    FILE_WRITE and CONTINUE need (Tests 046/049)."""
    code, _ = dev.call(P.REBOOT)
    if code:
        print('reboot: %s' % P.status_name(code)); return None
    dev.t.close()
    time.sleep(1.0)
    dev, port, text = connect(timeout)
    print('connected %s: %s' % (port, text))
    return dev


def put(dev, data, remote):
    """Write data to remote, streamed and renamed onto it (1.18, xy_forever/host_write.py); 0 only when written and
    read back exactly."""
    return host_write.put(dev, data, remote, sent_progress)


def screen_png(raw):
    """Framebuffer (480 columns stored right-to-left, 256 RGB565 pixels each) -> PNG bytes."""
    rows = []
    for y in range(SCREEN_H):
        row = bytearray(b'\0')                                  # PNG filter: none
        for x in range(SCREEN_W):
            v = struct.unpack_from('<H', raw, ((SCREEN_W - 1 - x) * SCREEN_STRIDE + y) * 2)[0]
            row += bytes(((v >> 11) << 3 | (v >> 13), ((v >> 5) & 63) << 2 | ((v >> 9) & 3), (v & 31) << 3 | ((v >> 2) & 7)))
        rows.append(bytes(row))

    def chunk(kind, body):
        return struct.pack('>I', len(body)) + kind + body + struct.pack('>I', zlib.crc32(kind + body) & 0xffffffff)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', SCREEN_W, SCREEN_H, 8, 2, 0, 0, 0)) +
            chunk(b'IDAT', zlib.compress(b''.join(rows), 9)) + chunk(b'IEND', b''))


def main(argv=None):
    try:
        return run(argv)
    except Unsupported as error:
        print('refused: %s' % error)
        return P.ids.S_UNSUPPORTED


def run(argv):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--timeout', type=float, default=30.0)
    parser.add_argument('--sim', metavar='HOST:PORT', help='talk to the device simulator (loopback TCP) instead of '
                        'the USB port; default: $XYF_SIM')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('wait')
    sub.add_parser('status')
    from xy_forever import a_rehearsal
    rehearsal = sub.add_parser('dev-a-rehearsal', help='RAM-only B-to-A transfer and admission; writes nothing')
    rehearsal.add_argument('input', help='verified XY Forever build directory, or stock os.ldr with --uninstall')
    rehearsal.add_argument('--uninstall', action='store_true')
    rehearsal.add_argument('--deadline-ms', type=int, default=120000)
    read = sub.add_parser('read'); read.add_argument('path'); read.add_argument('--out', type=Path)
    read.add_argument('--all', action='store_true', help='any size (ranged reads; needs the Test 038 service)')
    read.add_argument('--offset', type=int, help='one ranged read (up to READ_RANGE bytes) at this offset')
    inst = sub.add_parser('install'); inst.add_argument('build'); inst.add_argument('--yes-install', action='store_true')
    reboot = sub.add_parser('reboot'); reboot.add_argument('--yes-reboot', action='store_true')
    reboot.add_argument('--to', choices=('service', 'os', 'menu'), default='service',
                        help='Test 064: where the device goes (works from the service and from the menu)')
    bootp = sub.add_parser('boot', help='boot library entry ID once, as PLAY on it in the library menu (leaves '
                           'XY Forever: a power cycle with PLAY held comes back)')
    bootp.add_argument('id'); bootp.add_argument('--yes-boot', action='store_true')
    setd = sub.add_parser('set-default'); setd.add_argument('id'); setd.add_argument('--yes-write', action='store_true')
    putf = sub.add_parser('put-file', help='write a file under /yaffs2/xy-forever/ (fresh boot; creates missing folders)')
    putf.add_argument('local', type=Path); putf.add_argument('remote'); putf.add_argument('--yes-write', action='store_true')
    lsp = sub.add_parser('ls', help='list a device folder: type, size, name (file.list, read-only)')
    lsp.add_argument('path'); lsp.add_argument('--json', action='store_true')
    getp = sub.add_parser('get', help='copy a device file to the computer (ranged reads; size and ranges checked)')
    getp.add_argument('remote'); getp.add_argument('local', type=Path)
    putp = sub.add_parser('put', help='copy a file onto the OP-XY (streamed into tmp/, read back, renamed onto it)')
    putp.add_argument('local', type=Path); putp.add_argument('remote')
    putp.add_argument('--no-verify', action='store_true', help='skip the read-back over USB (the device still '
                      'compares the CRC-32 it reads back)')
    rmp = sub.add_parser('rm', help='delete a device file (--dir: an empty folder)')
    rmp.add_argument('path'); rmp.add_argument('--dir', action='store_true')
    mkd = sub.add_parser('mkdir', help='create a device folder (its parent must exist)')
    mkd.add_argument('path')
    mvp = sub.add_parser('mv', help='rename or move a device file or folder (both under /yaffs2/xy-forever/ or '
                         '/yaffs2/content/; the target must not exist)')
    mvp.add_argument('source'); mvp.add_argument('target')
    cpp = sub.add_parser('cp', help='copy a device file on the device (-r: a folder); the target must not exist and '
                         'be under /yaffs2/xy-forever/ or /yaffs2/content/')
    cpp.add_argument('source'); cpp.add_argument('target')
    cpp.add_argument('-r', dest='recursive', action='store_true', help='copy a folder and everything in it')
    shot = sub.add_parser('screenshot', help='save the display as PNG (read-only)')
    peek = sub.add_parser('peek', help='read stock DDR/L2/L1-data memory or SEC/core registers (read-only)')
    peek.add_argument('address', type=lambda s: int(s, 0)); peek.add_argument('bytes', type=lambda s: int(s, 0))
    peek.add_argument('--out', type=Path, required=True)
    shot.add_argument('--out', type=Path, required=True)
    from xy_forever import tfw_check_cli
    tfw_check_cli.configure(sub)
    library_add.configure(sub)
    sub.add_parser('save-times', help='print the save-time log (/yaffs2/xy-forever/log/save-times.txt) as a '
                   'table: one row per Save, USB build or content switch, milliseconds per part (read-only)')
    cont = sub.add_parser('continue')
    cont.add_argument('--dry-run', action='store_true',
                      help='product build: run the whole boot path, then come back to the service')
    keyp = sub.add_parser('key', help='menu over USB: press (and release) a key')
    keyp.add_argument('name', help='minus plus play stop shift track1..track8, or a key number')
    keyp.add_argument('--hold', action='store_true', help='press only (no release)')
    hold = sub.add_parser('hold', help='device stays in the service across reboots (until continue/power-off/unplug)')
    hold.add_argument('state', choices=('on', 'off'))
    mtpt = sub.add_parser('mtp-test', help="097: MTP's storage calls from b's main loop (a opens its read-write window), then home")
    mtpt.add_argument('--hold-ms', type=int, default=5000); mtpt.add_argument('--yes-test', action='store_true')
    uninst = sub.add_parser('uninstall'); uninst.add_argument('stock_os_ldr'); uninst.add_argument('--yes-uninstall', action='store_true')
    from xy_forever import modsend      # mods push|list|remove (xy_forever/modsend.py)
    modsend.add_arguments(sub.add_parser('mods', help='mod packages on the device: push, list, remove, apply, dev'))
    args = parser.parse_args(argv)
    global SIM
    if args.sim:
        sim_address(args.sim)
        SIM = args.sim
    if args.command == 'mods':
        if args.mods_command == 'dev':
            from xy_forever import moddev
            return moddev.command(args, connect, SLOTS, current_state)
        return modsend.command(args, connect)

    if args.command == 'install':
        data, notes = load_build(args.build)
        print('\n'.join(notes))
        print('Plan: stage %d bytes on processor b (CRC + SHA-256), then the device validates it,' % len(data))
        print('      writes the NAND boot partition with the stock commit-last writer (88a06a7e),')
        print('      and reads the active partition back sector by sector.')
        if not args.yes_install:
            print('DRY RUN: nothing sent. Re-run with --yes-install to install.')
            return 0
    if args.command in ('ls', 'get', 'rm', 'mkdir'):
        device_path(args.path if args.command != 'get' else args.remote)
    deep = depth.command_refusal(args)
    if deep:
        print(deep); return 2
    if args.command == 'mv' and not (in_write_area(args.source.rstrip('/')) and in_write_area(args.target.rstrip('/'))):
        print('refused: mv moves only %s (file.rename)' % AREA_TEXT); return 2
    if args.command == 'cp':
        source, target = device_path(args.source).rstrip('/'), args.target.rstrip('/')
        if not in_write_area(target):
            print('refused: cp writes only %s (file.copy)' % AREA_TEXT); return 2
        if target == source or target.startswith(source + '/'):
            print('refused: cp: the target %s is inside the source %s' % (target, source)); return 2
    if args.command == 'put':
        if not in_write_area(args.remote) or args.remote.endswith('/'):
            print('refused: put writes only files %s (file.write_staged)' % AREA_TEXT); return 2
        if not args.local.is_file():
            print('refused: %s is not a file' % args.local); return 2
    if args.command == 'put-file':
        if not WRITE_PATH_RE.match(args.remote) or '/../' in args.remote + '/' or '/./' in args.remote + '/':
            print('refused: remote path must be /yaffs2/xy-forever/<name>[/<name>...] ([A-Za-z0-9._-] names)')
            return 2
        data = args.local.read_bytes()
        if not data:
            print('refused: %s is empty' % args.local); return 2
        print('Plan: stream %d bytes (CRC-32 %08x, SHA-256) into /yaffs2/xy-forever/tmp/; processor b reads them' %
              (len(data), zlib.crc32(data) & 0xffffffff))
        print('      back and compares, then processor a renames the file onto %s in one step' % args.remote)
        print('      (replacing it). Missing folders are created first (mkdir, Test 050).')
        if not args.yes_write:
            print('DRY RUN: nothing sent. Re-run with --yes-write to write.')
            return 0
    if args.command.startswith('library-'):
        code, files = library_add.plan(args, IMAGE_CAPACITY)
        if files is None:
            return code
    if args.command == 'boot':
        if not ID_RE.match(args.id):
            print('refused: id must be 1-31 of [a-z0-9_-]'); return 2
        print('Plan: the device boots firmware/%s once, as PLAY on it in the library menu: the menu closes,' % args.id)
        print('      USB goes away and the OS does not answer this tool. The default and the state files stay.')
        print('      Back to XY Forever: power off, wait 5 s, power on holding PLAY, release it at the logo.')
        if not args.yes_boot:
            print('DRY RUN: nothing sent. Re-run with --yes-boot to boot.')
            return 0
    if args.command == 'set-default':
        if not ID_RE.match(args.id):
            print('refused: id must be 1-31 of [a-z0-9_-]'); return 2
        print('Plan: processor a writes the next A/B state slot (/yaffs2/xy-forever/state-a.txt or -b.txt)')
        print('      with default=%s, reads it back, then remounts read-only. Nothing else is written;' % args.id)
        print('      the entry must exist and have an os.ldr. Verified here by reading both slots back.')
        print('      An entry whose content is not installed gets it first: B rewrites /yaffs2/content/')
        print('      (the catalog, the mods\' files; official files, dlc/ and user/ are never touched), then the default.')
        if not args.yes_write:
            print('DRY RUN: nothing sent. Re-run with --yes-write to write.')
            return 0
    if args.command == 'uninstall':
        try:
            data, notes = load_stock(args.stock_os_ldr)
        except ValueError as error:
            print('refused: %s' % error); return 2
        print('\n'.join(notes))
        print('Plan: stage the stock 1.2.1 os.ldr on processor b, then the device checks its exact size and')
        print('      SHA-256, writes the NAND boot partition with the stock commit-last writer (88a06a7e, as')
        print('      dev-menu Track 5 does) and reads it back. Afterwards the OP-XY boots stock 1.2.1 and this')
        print('      USB service is gone; /yaffs2/xy-forever/ stays. The SPI bootloader is not touched.')
        if not args.yes_uninstall:
            print('DRY RUN: nothing sent. Re-run with --yes-uninstall to uninstall.')
            return 0
    if args.command == 'mtp-test' and not args.yes_test:
        print('DRY RUN: would put a into MTP mode (read-write window open, reads only), run MTP\'s storage calls')
        print('from b, unmount and return home. A hang needs a power cycle. Re-run with --yes-test.')
        return 0
    if args.command == 'reboot' and not args.yes_reboot:
        print('DRY RUN: would reboot both processors. Re-run with --yes-reboot.')
        return 0

    if args.command == 'dev-a-rehearsal':
        data, _ = (load_stock if args.uninstall else load_build)(args.input)
        if not 1 <= args.deadline_ms <= 120000:
            raise ValueError('deadline must be 1..120000 ms')

    dev, port, text = connect(args.timeout)
    print('connected %s: %s' % (port, text))
    hello = P.Hello(text)
    if hello.major != '1':
        print('refused: the device speaks protocol %s; this tool speaks 1.x (update xyf_host.py)' % hello.proto)
        return 2
    if args.command in ('wait', 'status'):
        print(hello.describe())
    if args.command == 'dev-a-rehearsal':
        code, state = a_rehearsal.run(dev, data, args.uninstall, args.deadline_ms, stage_progress)
        print(json.dumps(state, indent=2))
        return code
    if args.command == 'wait':
        return 0
    if args.command == 'status':
        code, body = dev.call(P.STATUS)
        print(json.dumps(decode_status(body), indent=2))
        return code
    if args.command == 'save-times':
        code, body = dev.read_all(SAVE_TIMES)
        if code:
            print('read failed: %s (no Save logged yet?)' % P.status_name(code)); return code
        print(save_times_table(body.decode('utf-8', 'replace')))
        return 0
    if args.command == 'read':
        code, body = (dev.read_all(args.path) if args.all else
                      dev.read_file(args.path, args.offset) if args.offset is not None else dev.read_file(args.path))
        if code:
            print('read failed: %s' % P.status_name(code)); return code
        if args.out:
            args.out.write_bytes(body)
        else:
            sys.stdout.write(body.decode('utf-8', 'replace'))
        return 0
    if args.command == 'install':
        code = dev.stage(data, stage_progress)
        print()
        if code:
            print('stage failed: %s (nothing installed)' % P.status_name(code)); return code
        code, written, partition, sectors = dev.install(hashlib.sha256(data).digest())
        print('install: %s, written %d, active partition %d, verified sectors %d' %
              (P.status_name(code), written, partition, sectors))
        return code
    if args.command == 'uninstall':
        code = dev.stage(data, stage_progress)
        print()
        if code:
            print('stage failed: %s (nothing written)' % P.status_name(code)); return code
        code, written, partition, sectors = dev.uninstall(hashlib.sha256(data).digest())
        print('uninstall: %s, written %d, active partition %d, verified sectors %d' %
              (P.status_name(code), written, partition, sectors))
        if code == 0:
            print('Power-cycle the OP-XY: it now boots stock 1.2.1.')
        return code
    if args.command == 'put-file':
        return put(dev, args.local.read_bytes(), args.remote)
    if args.command in ('ls', 'get', 'put', 'rm', 'mkdir', 'mv'):
        return files_command(dev, hello, args)
    if args.command == 'cp':
        from xy_forever import filecopy
        return filecopy.copy_command(dev, args.source.rstrip('/'), args.target.rstrip('/'), args.recursive)
    if args.command == 'tfw-check':
        from xy_forever import tfw_check_cli
        return tfw_check_cli.command(dev, args, TFW_CHECK_TIMEOUT)
    if args.command == 'peek':
        data = bytearray()
        while len(data) < args.bytes:
            n = min(1024, args.bytes - len(data))
            code, body = dev.call(P.PEEK, struct.pack('<II', args.address + len(data), n))
            if code or len(body) != 4 + n:                       # call() strips the status word
                print('peek: %s at 0x%08x' % (P.status_name(code), args.address + len(data))); return code or 1
            data += body[4:]
        args.out.write_bytes(bytes(data))
        print('peek: %d bytes from 0x%08x -> %s' % (len(data), args.address, args.out))
        return 0
    if args.command == 'screenshot':
        raw = bytearray()
        while len(raw) < SCREEN_BYTES:
            code, body = dev.call(P.SCREEN, struct.pack('<I', len(raw)))
            if code or len(body) < 5:
                print('screenshot: %s at offset %d' % (P.status_name(code), len(raw))); return code or 1
            raw += body[4:]
        args.out.write_bytes(screen_png(bytes(raw[:SCREEN_BYTES])))
        print('screenshot: framebuffer %08x -> %s' % (struct.unpack_from('<I', body)[0], args.out))
        return 0
    if args.command.startswith('library-'):
        return library_add.write(dev, args, files, lambda d: fresh(d, args.timeout), put)
    if args.command == 'set-default':
        code, sent_hash = dev.set_default(args.id)
        print('set-default sent: %s (id hash %08x)' % (P.status_name(code), sent_hash))
        if code == P.ids.S_STARTED:                    # the content switch runs first, then the default
            from xy_forever import modsend
            f = modsend.wait(dev, 0)
            if f is None or f['phase'] != modsend.DONE:
                return 1
            modsend.served(dev)
            code = 0
        if code:
            return code
        # Test 048: ok means a answered again after its write (remounted read-only).
        slots = {}
        for path in SLOTS:
            rc, body = dev.read_file(path)
            slots[path] = body if rc == 0 else None
            if rc and rc != 8:                               # 8 = not found (a slot may not exist yet)
                print('  read %s: %s' % (path, P.status_name(rc)))
        best = current_state(slots)
        if best and best[2].get('default') == args.id:
            print('verified: %s seq %d has default=%s' % (best[0], best[1], args.id)); return 0
        print('NOT verified: newest valid slot %s' % (best,)); return 1
    if args.command == 'boot':
        code = dev.library_boot(args.id)
        print('boot %s: %s%s' % (args.id, P.status_name(code), ' (the device leaves the menu and boots it once)'
                                 if code == 0 else ''))
        return code
    if args.command == 'reboot':
        target = {'service': 0, 'os': 1, 'menu': 2}[args.to]
        code, _ = dev.call(P.REBOOT, struct.pack('<I', target) if target else b'')
        print('reboot: %s' % P.status_name(code)); return code
    if args.command == 'continue':
        # No --menu (CONTINUE modes 2/3, Test 058): on boot model v3 the menu is home (docs/PROTOCOL.md).
        payload = struct.pack('<I', 1) if args.dry_run else b''
        code, _ = dev.call(P.CONTINUE, payload)
        if code == P.STATE:
            # Test 046 (v2 service builds; boot model v3 never answers state here): a file was read
            # this boot, so a's storage cannot mount fresh for the file boot. REBOOT and continue.
            print('continue: storage was read this boot; rebooting first')
            dev = fresh(dev, args.timeout)
            if dev is None:
                return 11
            code, _ = dev.call(P.CONTINUE, payload)
        print('continue%s: %s' % (' (dry run)' if args.dry_run else '', P.status_name(code))); return code
    if args.command == 'key':
        key = KEYS.get(args.name, None)
        if key is None:
            key = int(args.name) if args.name.isdigit() else None
        if key is None or not 0 <= key < 64:
            print('refused: unknown key %r' % args.name); return 2
        code, _ = dev.call(P.KEY, struct.pack('<II', key, 1))
        if code == 0 and not args.hold:
            time.sleep(0.15)
            try:
                code, _ = dev.call(P.KEY, struct.pack('<II', key, 0))
            except OSError:
                # The press ended the menu (e.g. PLAY boots an entry) and released USB first.
                print('key %s (%d): pressed; the menu closed and released USB' % (args.name, key)); return 0
        print('key %s (%d): %s' % (args.name, key, P.status_name(code))); return code
    if args.command == 'hold':
        code, _ = dev.call(P.HOLD, struct.pack('<I', 1 if args.state == 'on' else 0))
        print('hold %s: %s' % (args.state, P.status_name(code))); return code
    if args.command == 'mtp-test':
        names = ('step', 'mounted', 'space', 'stat', 'dir', 'entries', 'closedir', 'fd', 'read', 'closed',
                 'wait_ms', 'stat2', 'space2', 'unmounted', 'entry_bytes', 'reserved')
        code, body = dev.call(P.MTP_TEST, struct.pack('<I', args.hold_ms), timeout=MTP_TEST_TIMEOUT + args.hold_ms / 1000.0)
        values = struct.unpack('<16I', body[:64]) if len(body) >= 64 else ()
        print(json.dumps(dict(status=P.status_name(code), **{n: (v if v < 0x80000000 else v - (1 << 32))
                                                              for n, v in zip(names, values)}), indent=1))
        return code
    return 0


def files_command(dev, hello, args):
    """ls, get, put, rm, mkdir, mv."""
    if args.command == 'mv':
        code, error = dev.rename(args.source.rstrip('/'), args.target.rstrip('/'))
        print('mv %s %s: %s%s%s' % (args.source, args.target, P.status_name(code), ' (error %d)' % error if error else '',
                                    depth.move_note(code)))
        return code
    if args.command == 'ls':
        code, entries = dev.list_dir(args.path)
        if code:
            print('ls %s: %s' % (args.path, P.status_name(code))); return code
        rows = [entry_dict(e) for e in entries]
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, indent=1))
        else:
            for r in rows:
                print('%-5s %10s  %s' % (r['type'], r['size'] if r['type'] == 'file' else '-', r['name']))
        return 0
    if args.command in ('rm', 'mkdir'):
        kind = P.ids.MAKE_DIR if args.command == 'mkdir' else P.ids.REMOVE_DIR if args.dir else P.ids.DELETE_FILE
        code, error = dev.change(kind, args.path)
        print('%s %s: %s%s' % (args.command, args.path, P.status_name(code), ' (error %d)' % error if error else ''))
        return code
    if args.command == 'get':
        start = time.monotonic()
        code, data = dev.read_all(args.remote, limit=1 << 40)
        if code:
            print('get %s: %s' % (args.remote, P.status_name(code))); return code
        seconds = time.monotonic() - start
        parent, name = split_path(args.remote)
        code, entries = dev.list_dir(parent)
        size = [s for k, s, n in entries if n == name.encode('utf-8') and k == 0]
        if code or len(size) != 1 or size[0] != len(data):
            print('get %s: NOT verified: %d B read, listing says %s' % (args.remote, len(data),
                                                                       size or P.status_name(code))); return 1
        for offset in sample_offsets(len(data)):
            code, body = dev.read_file(args.remote, offset)
            if code or body != data[offset:offset + READ_RANGE]:
                print('get %s: NOT verified: the range at %d differs on a second read' % (args.remote, offset))
                return code or 1
        args.local.write_bytes(data)
        print('get %s -> %s: %d B in %.2f s (%.1f KiB/s); size matches file.list, %d ranges read twice' % (
            args.remote, args.local, len(data), seconds, len(data) / 1024.0 / max(seconds, 1e-6),
            len(sample_offsets(len(data)))))
        return 0
    data = args.local.read_bytes()                                   # put
    start = time.monotonic()
    code = put(dev, data, args.remote)
    if code:
        return code
    seconds = time.monotonic() - start
    if not args.no_verify:
        code, back = dev.read_all(args.remote, limit=len(data) + READ_RANGE)
        if code or back != data:
            print('put %s: NOT verified: read back %s, %d B' % (args.remote, P.status_name(code), len(back)))
            return code or 1
    print('put %s: %d B in %.2f s (%.1f KiB/s)%s' % (args.remote, len(data), seconds,
                                                   len(data) / 1024.0 / max(seconds, 1e-6),
                                                   '' if args.no_verify else '; read back identical'))
    return 0


SAVE_TIMES = '/yaffs2/xy-forever/log/save-times.txt'   # target/xyf_save_log.hpp


def save_times_table(text):
    """The save-time log as a table: one column per record (oldest first), one row per key (ms for parts);
    rows that are 0 in every record are left out."""
    records = []
    for line in text.splitlines():
        pairs = [f.split('=', 1) for f in line.split() if '=' in f]
        if pairs:
            records.append(dict(pairs))
    if not records:
        return '(no records)'
    keys = []
    for r in records:
        keys += [k for k in r if k not in keys]
    rows = [[k] + [r.get(k, '') for r in records] for k in keys
            if any(r.get(k, '0') not in ('0', '') for r in records) or k in ('kind', 'total')]
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    return '\n'.join('  '.join(c.rjust(w) if i else c.ljust(w) for i, (c, w) in enumerate(zip(row, widths)))
                     for row in rows)


def decode_status(body):
    from xy_forever import census
    blocks, at = [], 0
    while at + 8 <= len(body):
        magic, size = struct.unpack_from('<II', body, at)
        blocks.append((magic, body[at + 8:at + 8 + size]))
        at += 8 + size
    regions, dump = [], b''
    for _, data in blocks:
        regions.append(dict(address=0, bytes=len(data), offset=len(dump)))
        dump += data
    return dict(usb_service=census.usb_service(regions, dump), remote_read=census.remote_read(regions, dump),
                boot_timing=census.boot_timing(regions, dump),
                storage_start=census.storage_start(regions, dump),     # 191
                dry_run_file_boot=census.file_boot(regions, dump),
                knob_input=next((k for k in map(census.knob_summary, (d for _, d in blocks)) if k), None),
                blocks=['0x%08x:%d' % (m, len(d)) for m, d in blocks])


if __name__ == '__main__':
    sys.exit(main())
