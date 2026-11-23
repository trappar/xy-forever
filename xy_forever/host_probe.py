"""What the menu observed, for xyf_host.py (#288): its raw input events, its frame times, free storage.

All read-only: the events and frame times come from diag.status (blocks INP1, FRM1), the free space from
storage.space.
"""

import struct
import time

from . import census, host_inventory
from . import usb_proto as P

WATCH_S = 0.2  # --watch: the pause between two diag.status reads


def say(line):
    """print, flushed: a redirected --watch shows each event as it comes."""
    print(line, flush=True)


def configure(sub):
    inputs = sub.add_parser(
        'inputs', help="the menu's last 32 raw key events and knob records, oldest first (diag.status, read-only)"
    )
    inputs.add_argument('--watch', action='store_true', help='keep reading: print each new event (Ctrl-C ends)')
    sub.add_parser('frames', help="the menu's frame times: draw, present, the display DMA (diag.status, read-only)")
    sub.add_parser('space', help='free storage on /yaffs2 in bytes (storage.space, read-only)')


def blocks(body):
    """diag.status's blocks: {magic: data} (the first of each)."""
    out, at = {}, 0
    while at + 8 <= len(body):
        magic, size = struct.unpack_from('<II', body, at)
        out.setdefault(magic, body[at + 8 : at + 8 + size])
        at += 8 + size
    return out


def read_status(dev):
    code, body = dev.call(P.STATUS)
    if code:
        raise OSError('diag.status: %s' % P.status_name(code))
    return blocks(body)


def input_log(dev):
    log = census.input_events(read_status(dev).get(census.INPUT_MAGIC, b''))
    if log is None:
        raise OSError('this build has no input log (diag.status block INP1)')
    return log


def inputs(dev, watch, out=say, sleep=time.sleep):
    count, events = input_log(dev)
    out('%d events logged since the menu started%s' % (count, '' if count <= len(events) else ' (the last 32:)'))
    for event in events:
        out(census.input_line(event))
    while watch:
        sleep(WATCH_S)
        newer, events = input_log(dev)
        if newer < count:
            out('(the log started again: a new boot)')
            count = 0
        if newer - count > len(events):
            out('(%d events came faster than this read them)' % (newer - count - len(events)))
        for event in events:
            if event['n'] >= count:
                out(census.input_line(event))
        count = newer
    return 0


def frames(dev, out=say):
    f = census.frame_time(read_status(dev).get(census.FRAMES_MAGIC, b''))
    if f is None:
        raise OSError('this build reports no frame times this tool reads (diag.status block FRM1, version 2)')
    if not f['presents']:
        out('no frame measured yet (the menu has not drawn)')
        return 0
    out('frames measured: %d' % f['presents'])
    for name, label in (
        ('draw', 'draw (480x216 repaint + cache flush)'),
        ('transfer', 'transfer (the stock send, until its DMA ends)'),
        ('present', 'present (all of it: wait + draw + transfer)'),
    ):
        out(
            '%-46s last %6d us  min %6d us  max %6d us'
            % (label, f[name + '_last_us'], f[name + '_min_us'], f[name + '_max_us'])
        )
    out(
        '%-46s last %6d us  max %6d us' % ('of that, a DMA still running at entry', f['wait_last_us'], f['wait_max_us'])
    )
    # present() is synchronous end to end: the menu loop does nothing else meanwhile, so a frame costs all of it.
    period = f['present_max_us']
    out(
        'a frame costs the whole present, the loop blocked: at most %.0f frames/s (%d us each)' % (1e6 / period, period)
    )
    return 0


def space(dev, out=say):
    free = host_inventory.free_bytes(dev)
    out('free: %d bytes (%.1f MiB)' % (free, free / 2**20))
    return 0


def command(dev, args):
    try:
        if args.command == 'inputs':
            return inputs(dev, args.watch)
        if args.command == 'frames':
            return frames(dev)
        return space(dev)
    except (OSError, ValueError) as error:
        print('%s: %s' % (args.command, error))
        return 1
    except KeyboardInterrupt:
        return 0
