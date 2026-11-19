"""Where an XY Forever MTP transfer's time goes (#144): diag.mtp op 7 (target/usb_service_mtp_session.ipp
mtp_timing), read over the serial service beside MTP (the composite's CDC, or the simulator's connection).

B's counters (mtp_session.hpp Timing) and A's (storage_server_timing.ipp) count cycles of the 400 MHz core
clock: sums in units of 1024 cycles (2.56 us), longest in cycles. clear() before a transfer, read() after it,
lines() for the breakdown. Bounded waits only; nothing here changes storage.
"""
import struct
import time

TUNNEL = 0x61                         # diag.mtp
OP = 7
UNIT_S = 1024 / 400e6                 # one unit, seconds
CYCLE_US = 1 / 400.0                  # one cycle, microseconds
B_WORDS = ('reads', 'read_units', 'read_max', 'writes', 'write_units', 'write_max', 'calls', 'call_units',
           'steps', 'step_units', 'pumps', 'pump_units', 'waits', 'read_bytes', 'write_bytes', 'started',
           'loops', 'loop_units', 'draws', 'draw_units')
A_WORDS = ('reads', 'read_units', 'read_max', 'writes', 'write_units', 'write_max',
           'others', 'other_units', 'gaps', 'gap_units', 'gap_max', 'checks')
# A's other requests by operation (file_engine.hpp TimingSlot, in order): count, sum (units), longest (cycles) and
# A's own checks in them (units: stats, the engine spans' CRC; the rest is the stock executor's) each
A_OPS = ('mount (op 8)', 'window close (op 9)', 'access', 'rename from (RNFR)', 'rename to (RNTO/RPTO/RPUT)', 'stat',
         'statfs', 'unlink', 'mkdir', 'rmdir', 'open', 'seek', 'close', 'opendir', 'readdir', 'closedir', 'other',
         'window open (+ its request)')
PIECES = (1024, 4096)                 # the transfer piece: chunk .. the responder's buffer
DEFAULT_PIECE = 4096                  # the device's default (mtp_responder.hpp default_piece)


class TimingError(Exception):
    """diag.mtp op 7 failed (one line)."""


def call(dev, body, timeout=10.0):
    """(status, body) of diag.mtp op 7 (not in the hello list: the check is skipped for it)."""
    info, dev.hello_info = dev.hello_info, None
    try:
        return dev.call(TUNNEL, bytes([OP]) + body, timeout=timeout)
    finally:
        dev.hello_info = info


def read(dev, wait=3.0, sleep=time.sleep, clock=time.monotonic):
    """{'piece', 'b_now', 'b': {...}, 'a': {...} or None, 'a_ops': {name: (count, units, longest)} or None};
    waits up to wait s for A's words (A is asked only while nothing holds the file client: a window close just
    after an upload, say)."""
    deadline = clock() + wait
    while True:
        code, body = call(dev, b'\0')
        if code:
            raise TimingError('diag.mtp timing: status %d' % code)
        need = 8 + 4 * len(B_WORDS) + 4 + 4 * len(A_WORDS)
        if len(body) < need:
            raise TimingError('diag.mtp timing: %d bytes, expected %d' % (len(body), need))
        piece, now = struct.unpack_from('<2I', body)
        b = dict(zip(B_WORDS, struct.unpack_from('<%dI' % len(B_WORDS), body, 8)))
        at = 8 + 4 * len(B_WORDS)
        a_ok, = struct.unpack_from('<I', body, at)
        a = dict(zip(A_WORDS, struct.unpack_from('<%dI' % len(A_WORDS), body, at + 4))) if a_ok else None
        if a is not None or clock() >= deadline:
            return dict(piece=piece, b_now=now, b=b, a=a, a_ops=read_ops(dev) if a is not None else None)
        sleep(0.1)


def read_ops(dev):
    """A's other requests by operation (op 7 sub 3): {name: (count, units, longest, check units)} of those that
    ran, or None (A not asked now, or a build without them)."""
    code, body = call(dev, b'\3')
    if code or len(body) < 4 + 16 * len(A_OPS) or not struct.unpack_from('<I', body)[0]:
        return None
    words = struct.unpack_from('<%dI' % (4 * len(A_OPS)), body, 4)
    return {name: words[4 * i:4 * i + 4] for i, name in enumerate(A_OPS) if words[4 * i]}


def clear(dev, wait=3.0, sleep=time.sleep, clock=time.monotonic):
    """Zero B's and A's counters; True when A's were cleared too (else A's words are totals since boot)."""
    deadline = clock() + wait
    while True:
        code, body = call(dev, b'\1')
        if code:
            raise TimingError('diag.mtp timing clear: status %d' % code)
        cleared = len(body) >= 4 and bool(struct.unpack_from('<I', body)[0])
        if cleared or clock() >= deadline:
            return cleared
        sleep(0.1)


def set_piece(dev, piece):
    """The responder's transfer piece (bytes, PIECES); the device's answer."""
    code, body = call(dev, b'\2' + struct.pack('<I', piece))
    if code or len(body) < 4:
        raise TimingError('diag.mtp piece %d: status %d' % (piece, code))
    return struct.unpack_from('<I', body)[0]


def _ms(units):
    return units * UNIT_S * 1e3


def _avg_us(units, count):
    return units * UNIT_S * 1e6 / count if count else 0.0


def lines(t, seconds, label):
    """The breakdown of one transfer phase (t: read() after clear(); seconds: the host's wall time)."""
    b, a = t['b'], t['a']
    wall = max(seconds, 1e-9) * 1e3
    out = ['%s: %.0f ms on the host, piece %d B' % (label, wall, t['piece'])]

    def row(name, count, units, longest=None, note=''):
        text = '  %-34s %6d x %8.1f us = %8.1f ms %5.1f%%' % (name, count, _avg_us(units, count), _ms(units),
                                                             100 * _ms(units) / wall)
        if longest is not None:
            text += '  (longest %.0f us)' % (longest * CYCLE_US)
        out.append(text + ('  ' + note if note else ''))

    data_units = b['read_units'] + b['write_units']
    if b['writes']:
        row('B write requests (B waits for A)', b['writes'], b['write_units'], b['write_max'],   # left out (#144)
            '%d B each' % (b['write_bytes'] // b['writes']))
    if b['reads']:
        row('B read requests (round trip)', b['reads'], b['read_units'], b['read_max'],
            '%d B each' % (b['read_bytes'] // b['reads']))
    if a is not None:
        if a['writes']:
            row('  A write executor (storage)', a['writes'], a['write_units'], a['write_max'])
        if a['reads']:
            row('  A read executor (storage)', a['reads'], a['read_units'], a['read_max'])
        if b['reads']:                                     # writes overlap A's work (#144): reads only
            row('  link and queues (round trip - A)', b['reads'], max(0, b['read_units'] - a['read_units']))
        row('A idle between requests (< 20 ms)', a['gaps'], a['gap_units'], a['gap_max'])
        row('A other requests', a['others'], a['other_units'])
        for name, (count, units, longest, checks) in (t.get('a_ops') or {}).items():
            row('  ' + name, count, units, longest, 'A\'s checks %.0f us each' % _avg_us(checks, count))
        if a['others'] and b['calls']:                     # A's dispatch latency and the link, per other request
            row('  link and queues (B calls - A)', a['others'], max(0, b['call_units'] - a['other_units']))
        if a['checks']:
            out.append('  %-34s %6d  (stats, the engine spans\' CRC: A\'s own, inside the requests above)' % (
                'A checks', a['checks']))
    else:
        out.append('  (A\'s counters not read: the file client was busy)')
    row('B other file calls', b['calls'], b['call_units'])
    row('B responder steps, file calls aside', b['steps'], max(0, b['step_units'] - data_units - b['call_units']))
    row('B USB side (driver passes)', b['pumps'], b['pump_units'])
    # #144: the menu loop outside its USB pass while MTP traffic runs (its pause, redraws, the link tick)
    row('B menu loop between USB passes', b['loops'], b['loop_units'], note='pause, redraws, link tick, keys')
    row('  screen redraws', b['draws'], b['draw_units'])
    rest = wall - _ms(b['step_units'] + b['pump_units'] + b['loop_units'])
    out.append('  %-34s %31.1f ms %5.1f%%  (host, USB waits, B outside MTP traffic; %d idle steps)' % (
        'rest', rest, 100 * rest / wall, b['waits']))
    return out
