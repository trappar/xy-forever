#!/usr/bin/env python3
"""Read and write speed of the OP-XY's file routes, measured with one set of files (docs/SAFETY.md, "Host access").

Routes:
  serial    XY Forever's serial file commands (xyf_host.py: put, the ranged reads of `get`)
  xyf-mtp   XY Forever's MTP mode (USB; under --sim the dev command diag.mtp tunnels it)
  os-mtp    the stock OS's MTP (its storage is the OS's /fat32, not /yaffs2: docs/hardware/devices.md)
  dev-mtp   the stock dev menu's MTP (COM held at power-on, Track 3, Track 2)
Each route writes the set into the scratch folder /yaffs2/throughput-scratch/ (MTP: /throughput-scratch at the
storage root), reads it back, compares it, then removes the scratch folder, also after a failure; what was
removed is printed for the cleanup log (docs/operating/at-the-device.md). Leftovers of an earlier run in that
folder are removed first. MTP writes go through xy_forever/mtp_write.py (its checks: /yaffs2 only, never
bootloader.dev nor upgrade/, the depth rule, deletes only in the scratch folder). This script cannot switch the
OP-XY into a stock mode: it says what to do and waits for Enter (bounded), or --ready says it already is.
Every wait is bounded. One table row per route; exit 0 only when every route passed.

  python3 scripts/throughput.py                             # all four routes, asking for the stock modes
  python3 scripts/throughput.py --route serial --small-count 50
  python3 scripts/throughput.py --sim 127.0.0.1:7341        # serial and xyf-mtp against python3 -m xy_forever.sim
  python3 scripts/throughput.py --route xyf-mtp --piece 1024 # XY Forever's MTP with 1 KiB pieces per A request

After the xyf-mtp route, where its time went (#144, xy_forever/mtp_timing.py): for each phase (big files written,
small files written, read back), B's file requests to A (their round trips), A's own storage time per request,
the link and queues between them, A's other requests by operation (open, close, the rename pair, mkdir, window
open and close...), B's responder and USB work, and the rest. It is read over the serial service
beside MTP (diag.mtp op 7); without it (--no-breakdown, or no serial) only the table is printed.
"""
import argparse
import contextlib
import io
import os
import random
import re
import select
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import xyf_host as host  # noqa: E402
from xy_forever import mtp_read as M, mtp_timing as T, mtp_write as W  # noqa: E402

ROUTES = ('serial', 'xyf-mtp', 'os-mtp', 'dev-mtp')
SIM_ROUTES = ('serial', 'xyf-mtp')
SCRATCH = '/yaffs2/throughput-scratch'      # not xy-forever/tmp/ (swept by the device); 1 folder below /yaffs2
STOCK = {
    'os-mtp': ('the stock OS\'s MTP: boot the stock OS (XY Forever: Continue into stock firmware) with USB '
               'connected; quit Field Kit, OpenMTP and Image Capture', 'leave the stock OS as it is'),
    'dev-mtp': ('the stock dev menu\'s MTP: switch the OP-XY off, hold COM while switching it on, Track 3 '
                '("3. dev menu"), Track 2 ("2. mtp file transfer (mac)"); wait for "mtp mode active"',
                'press Track 2 ("2. exit"), then switch the OP-XY off'),
}
TUNNEL = 0x61                               # diag.mtp (tests/test_sim_mtp_session.py)
NOT_FOUND = host.P.STATUS_NAMES.index('not_found')


class Failed(Exception):
    """One line for the table."""


ERRORS = (Failed, M.MtpError, OSError, TimeoutError, host.Unsupported, T.TimingError)


# the file set and the table

def parse_size(text):
    """'5M', '4K', '512' -> bytes (K and M are 1024-based)."""
    match = re.fullmatch(r'(\d+)\s*([KkMm]?)i?[Bb]?', text.strip())
    if not match:
        raise argparse.ArgumentTypeError('not a size: %r (e.g. 4K, 5M)' % text)
    return int(match.group(1)) << {'': 0, 'k': 10, 'm': 20}[match.group(2).lower()]


def file_set(big, big_count, small, small_count, seed=1):
    """[(name below the scratch folder, data, small?)]: big files at the top, small ones in small/."""
    rng = random.Random(seed)
    return ([('big-%d.bin' % i, rng.randbytes(big), False) for i in range(big_count)] +
            [('small/s-%03d.bin' % i, rng.randbytes(small), True) for i in range(small_count)])


class Result:
    def __init__(self, route, files):
        self.route, self.error = route, None
        self.count = {s: sum(1 for f in files if f[2] == s) for s in (False, True)}
        self.bytes = {s: sum(len(f[1]) for f in files if f[2] == s) for s in (False, True)}
        self.seconds = {}                   # ('write' | 'read', small?) -> seconds
        self.removed, self.leftovers, self.cleanup_error = [], [], None
        self.breakdown = []                 # xyf-mtp: where the time went (lines)


def rate(amount, seconds):
    return '%.1f' % (amount / seconds) if seconds and seconds > 0 else '-'


COLUMNS = ('route', 'write KiB/s', 'read KiB/s', 'write files/s', 'read files/s', 'files', 'KiB', 'seconds')


def table(results):
    """The result table (KiB/s: the big files; files/s: the small set; totals: everything moved both ways)."""
    rows = [COLUMNS]
    for r in results:
        if r.error:
            rows.append((r.route, 'failed: ' + r.error))
            continue
        s = r.seconds
        rows.append((r.route, rate(r.bytes[False] / 1024.0, s.get(('write', False))),
                     rate(r.bytes[False] / 1024.0, s.get(('read', False))),
                     rate(r.count[True], s.get(('write', True))), rate(r.count[True], s.get(('read', True))),
                     str(r.count[False] + r.count[True]), '%.0f' % ((r.bytes[False] + r.bytes[True]) / 1024.0),
                     '%.2f' % sum(s.values())))
    widths = [max(len(row[i]) for row in rows if len(row) > i and len(row) == len(COLUMNS))
              for i in range(len(COLUMNS))]
    out = []
    for row in rows:
        if len(row) != len(COLUMNS):
            out.append('%-*s  %s' % (widths[0], row[0], row[1]))
        else:
            out.append('  '.join(c.ljust(w) if i == 0 else c.rjust(w) for i, (c, w) in enumerate(zip(row, widths))))
    return '\n'.join(out)


def cleanup_lines(r):
    """What a route removed from the device, for the cleanup log (docs/operating/at-the-device.md)."""
    lines = []
    if r.leftovers:
        lines.append('%s: removed %d leftovers of an earlier run in %s' % (r.route, len(r.leftovers), SCRATCH))
    if r.removed:
        lines.append('%s: removed %s (%d paths: the test files, their folders and the folder)' % (
            r.route, SCRATCH, len(r.removed)))
    if r.cleanup_error:
        lines.append('%s: cleanup FAILED (%s): %s may be left on the device' % (r.route, r.cleanup_error, SCRATCH))
    return lines


def measure(route, ops, files, clock=time.monotonic):
    """Write the set, read it back and compare, then clean up (always). ops: prepare() -> leftovers removed,
    write(path, data) -> token, read(token, path, size) -> bytes, cleanup() -> paths removed."""
    r = Result(route, files)
    try:
        r.leftovers = ops.prepare()
        tokens = {}
        phase = getattr(ops, 'phase', None)                                # xyf-mtp: the breakdown
        for kind in ('write', 'read'):
            for small in (False, True):
                if phase:
                    phase(r, kind, small, None)
                start = clock()
                for name, data, is_small in files:
                    if is_small != small:
                        continue
                    path = SCRATCH + '/' + name
                    if kind == 'write':
                        tokens[name] = ops.write(path, data)
                    elif ops.read(tokens[name], path, len(data)) != data:
                        raise Failed('%s read back differs' % path)
                r.seconds[(kind, small)] = clock() - start
                if phase:
                    phase(r, kind, small, r.seconds[(kind, small)])
    except ERRORS as error:
        r.error = str(error) or type(error).__name__
    except KeyboardInterrupt:
        r.error = 'interrupted'
    try:
        r.removed = ops.cleanup()
    except ERRORS as error:
        r.cleanup_error = str(error) or type(error).__name__
    return r


# serial: XY Forever's own file commands

def serial_tree(dev, path):
    """Delete a folder below SCRATCH (or SCRATCH) and everything in it, deepest first: the paths removed."""
    if path != SCRATCH and not path.startswith(SCRATCH + '/'):
        raise Failed('refused: %s is outside %s' % (path, SCRATCH))
    code, entries = dev.list_dir(path)
    if code == NOT_FOUND:
        return []
    if code:
        raise Failed('ls %s: %s' % (path, host.P.status_name(code)))
    removed = []
    for kind, _, name in entries:
        child = path + '/' + name.decode('utf-8')
        if kind == 1:
            removed += serial_tree(dev, child)
        else:
            change(dev, host.P.ids.DELETE_FILE, child)
            removed.append(child)
    change(dev, host.P.ids.REMOVE_DIR, path)
    return removed + [path]


def change(dev, kind, path):
    code, error = dev.change(kind, path)
    if code:
        raise Failed('%s %s: %s%s' % (host.P.ids.COMMANDS[kind]['name'], path, host.P.status_name(code),
                                      ' (error %d)' % error if error else ''))


class SerialOps:
    def __init__(self, dev):
        self.dev = dev

    def prepare(self):
        leftovers = serial_tree(self.dev, SCRATCH)
        for folder in (SCRATCH, SCRATCH + '/small'):
            change(self.dev, host.P.ids.MAKE_DIR, folder)
        return leftovers

    def write(self, path, data):
        out = io.StringIO()                         # put prints its progress
        with contextlib.redirect_stdout(out):
            code = host.put(self.dev, data, path)
        if code:
            lines = out.getvalue().strip().splitlines()
            raise Failed('put %s: %s%s' % (path, host.P.status_name(code), ' (%s)' % lines[-1] if lines else ''))
        return path

    def read(self, token, path, size):
        code, back = self.dev.read_all(path, limit=size + host.READ_RANGE)
        if code:
            raise Failed('read %s: %s' % (path, host.P.status_name(code)))
        return back

    def cleanup(self):
        return serial_tree(self.dev, SCRATCH)

    def close(self):
        self.dev.t.close()


# MTP: mtp_write.Writer over USB or the simulator's tunnel

class MtpOps:
    def __init__(self, transport, timing=None, breakdown=True, piece=None):
        self.transport, self.where = transport, {}
        self.timing = timing                # xyf-mtp: a serial Device for diag.mtp op 7, or None
        self.breakdown, self.piece = breakdown, piece   # the breakdown wanted; a piece set (restored on close)
        self.w = W.Writer(transport, SCRATCH)
        try:
            self.w.open()
        except BaseException:
            transport.close()
            raise

    def prepare(self):
        leftovers = self.w.remove_tree()
        for folder in (SCRATCH, SCRATCH + '/small'):
            self.where[folder] = self.w.mkdir(folder)
        return leftovers

    def write(self, path, data):
        return self.w.create(path, data, self.where[path.rsplit('/', 1)[0]])[1]

    def read(self, token, path, size):
        return self.w.get(token)

    def cleanup(self):
        return self.w.remove_tree()

    def phase(self, r, kind, small, seconds):
        """Before a phase (seconds None): the device's timing cleared; after it: its breakdown into r."""
        if not self.timing or not self.breakdown:
            return
        label = '%s, %s %s' % (r.route, 'small files' if small else 'big files', 'written' if kind == 'write' else
                               'read')
        try:
            if seconds is None:
                T.clear(self.timing)
            else:
                r.breakdown += T.lines(T.read(self.timing), seconds, label)
        except ERRORS as error:
            r.breakdown.append('%s: no breakdown (%s)' % (label, error))

    def close(self):
        try:
            if self.piece and self.piece != T.DEFAULT_PIECE:
                T.set_piece(self.timing, T.DEFAULT_PIECE)               # the default again
        finally:
            self.w.close()
            self.transport.close()
            if self.timing and self.timing is not getattr(self.transport, 'dev', None):
                self.timing.t.close()


class Tunnel:
    """An mtp_read transport over the simulator's diag.mtp (0x61): op 0 enters the MTP session, 1 leaves it,
    2 is one OUT transfer (at most 4096 bytes; retried while busy), 3 takes IN bytes, 5 is the state."""
    OUT_MAX = 4096

    def __init__(self, dev, timeout=20.0):
        self.dev, self.timeout = dev, timeout
        self.buffer, self.left = bytearray(), 0             # IN bytes taken, not yet read; left in this container

    def call(self, op, body=b''):
        info, self.dev.hello_info = self.dev.hello_info, None              # diag.mtp is not in the hello list
        try:
            return self.dev.call(TUNNEL, bytes([op]) + body, timeout=10.0)
        finally:
            self.dev.hello_info = info

    def enter(self):
        code, _ = self.call(0)
        if code:
            raise Failed('diag.mtp enter: %s' % host.P.status_name(code))
        return self

    def write(self, data):
        for at in range(0, len(data), self.OUT_MAX):                       # empty (a ZLP): nothing to carry
            deadline = time.monotonic() + self.timeout
            while True:
                code, _ = self.call(2, data[at:at + self.OUT_MAX])
                if code == host.P.ids.S_OK:
                    break
                if code != host.P.ids.S_BUSY or time.monotonic() > deadline:
                    return -7                                              # as libusb's timeout
                time.sleep(0.002)
        return 0

    def read(self, size):
        """Like a bulk IN transfer: a container's first bytes always carry its whole 12-byte header (op 3 hands
        out what B has staged, which may be less)."""
        deadline = time.monotonic() + self.timeout
        while not (self.buffer and (self.left or len(self.buffer) >= 12)):
            if time.monotonic() > deadline:
                return -7, b''
            code, body = self.call(3, struct.pack('<I', self.OUT_MAX))
            if code:
                return -1, b''
            if len(body) > 4:
                self.buffer += body[4:]
            else:
                time.sleep(0.002)
        if not self.left:
            self.left = max(12, struct.unpack_from('<I', self.buffer)[0])
        n = min(size, len(self.buffer), self.left)
        got, self.buffer, self.left = bytes(self.buffer[:n]), self.buffer[n:], self.left - n
        return 0, got

    def close(self):
        try:
            self.call(1)
            deadline = time.monotonic() + 60.0
            while time.monotonic() < deadline:
                code, body = self.call(5)
                if code or len(body) < 36 or not struct.unpack_from('<9I', body)[8]:
                    break
                time.sleep(0.02)
        finally:
            self.dev.t.close()


def open_usb_mtp(route, timeout_ms):
    """The OP-XY's MTP over libusb, checked to be the route asked for: XY Forever's MTP sits beside its serial
    (CDC) interfaces in the composite; the stock modes have no CDC interface."""
    lines = []
    transport = M.open_op_xy(timeout_ms, log=lines.append)
    classes = {int(c) for c in re.findall(r'interface \d+ class (\d+)/', '\n'.join(lines))}
    composite = bool(classes & {2, 10})
    if composite != (route == 'xyf-mtp'):
        transport.close()
        raise Failed('the OP-XY\'s MTP on USB is %s, not %s' % (
            'XY Forever\'s' if composite else 'a stock mode\'s', route))
    return transport


def wait_for_enter(text, seconds, stream=None):
    stream = stream or sys.stdin
    print('%s.\nPress Enter when it does (waiting at most %d s).' % (text, seconds), flush=True)
    try:
        ready, _, _ = select.select([stream], [], [], seconds)
    except (OSError, ValueError):
        ready = []
    if not ready or not stream.readline():
        raise Failed('no Enter within %d s' % seconds)


def open_route(route, args):
    """The ops of one route, connected (bounded waits)."""
    if route == 'serial':
        if args.sim:
            host.SIM = args.sim
        with contextlib.redirect_stdout(io.StringIO()):
            dev, _, _ = host.connect(args.connect_timeout)
        dev.on_log = None
        return SerialOps(dev)
    if route == 'xyf-mtp' and args.sim:
        dev = host.Device(host.TcpPort(args.sim), on_log=None)
        try:
            code, text = dev.hello()
            if code:
                raise Failed('hello: %s' % host.P.status_name(code))
            tunnel = Tunnel(dev).enter()
            return MtpOps(tunnel, dev, args.breakdown, piece(dev, args))
        except BaseException:
            dev.t.close()
            raise
    if route in STOCK and not args.ready:
        wait_for_enter('Put the OP-XY in %s' % STOCK[route][0], args.wait)
    transport = open_usb_mtp(route, args.timeout_ms)
    if route != 'xyf-mtp' or not (args.breakdown or args.piece):
        return MtpOps(transport)
    try:                                                                   # the serial beside it (the composite)
        with contextlib.redirect_stdout(io.StringIO()):
            dev, _, _ = host.connect(args.connect_timeout)
        dev.on_log = None
    except (SystemExit,) + ERRORS as error:
        if args.piece:
            transport.close()
            raise Failed('--piece needs the serial service beside MTP: %s' % error)
        print('xyf-mtp: no breakdown (serial: %s)' % error, flush=True)
        return MtpOps(transport)
    try:
        return MtpOps(transport, dev, args.breakdown, piece(dev, args))
    except BaseException:
        dev.t.close()
        raise


def piece(dev, args):
    """--piece set on the device (until the route ends), or None."""
    if args.piece:
        print('xyf-mtp: piece %d B' % T.set_piece(dev, args.piece), flush=True)
    return args.piece


def select_routes(route, sim):
    """The routes to run, in order; SystemExit with one line when the choice cannot run."""
    if sim and route and route not in SIM_ROUTES:
        raise SystemExit('%s is device only: the simulator has no stock OS and no dev menu (--sim runs %s)' %
                         (route, ' and '.join(SIM_ROUTES)))
    return [route] if route else list(SIM_ROUTES if sim else ROUTES)


def run_route(route, args, files):
    try:
        ops = open_route(route, args)
    except ERRORS as error:
        r = Result(route, files)
        r.error = str(error) or type(error).__name__
        return r
    except SystemExit as error:                                            # host.connect: no service found
        r = Result(route, files)
        r.error = str(error.code)
        return r
    try:
        return measure(route, ops, files)
    finally:
        try:
            ops.close()
        except ERRORS:
            pass
        if route in STOCK:
            print('%s done: %s.' % (route, STOCK[route][1]), flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    parser.add_argument('--route', choices=ROUTES, help='one route only (default: all; under --sim: %s)' %
                        ', '.join(SIM_ROUTES))
    parser.add_argument('--sim', metavar='HOST:PORT', help='the device simulator (python3 -m xy_forever.sim)')
    parser.add_argument('--big', type=parse_size, default=5 << 20, help='big file size (default 5M)')
    parser.add_argument('--big-count', type=int, default=1, help='big files (default 1)')
    parser.add_argument('--small', type=parse_size, default=4 << 10, help='small file size (default 4K)')
    parser.add_argument('--small-count', type=int, default=200, help='small files (default 200)')
    parser.add_argument('--ready', action='store_true', help='the OP-XY is already in the stock mode asked for')
    parser.add_argument('--wait', type=int, default=300, help='seconds to wait for Enter (default 300)')
    parser.add_argument('--connect-timeout', type=float, default=30.0, help='serial connect, seconds (default 30)')
    parser.add_argument('--timeout-ms', type=int, default=10000, help='one USB transfer, ms (default 10000)')
    parser.add_argument('--piece', type=int, choices=T.PIECES,
                        help='xyf-mtp: bytes per file request to A for this route (#144; default 4096)')
    parser.add_argument('--no-breakdown', dest='breakdown', action='store_false',
                        help='xyf-mtp: no timing breakdown (diag.mtp op 7)')
    args = parser.parse_args(argv)
    if args.big_count < 0 or args.small_count < 0 or not args.big_count + args.small_count:
        parser.error('nothing to move')
    routes = select_routes(args.route, args.sim)
    files = file_set(args.big, args.big_count, args.small, args.small_count)
    results = []
    for route in routes:
        print('%s: %d files, %d KiB ...' % (route, len(files), sum(len(f[1]) for f in files) // 1024), flush=True)
        results.append(run_route(route, args, files))
        for line in cleanup_lines(results[-1]) + results[-1].breakdown:
            print(line)
    print(table(results))
    return 0 if all(not r.error and not r.cleanup_error for r in results) else 1


if __name__ == '__main__':
    sys.exit(main())
