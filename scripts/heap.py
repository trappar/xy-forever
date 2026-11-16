#!/usr/bin/env python3
"""Show B's stock heap through XY Forever's PEEK (host only, read-only).

usage: python3 scripts/heap.py [--timeout S]

Prints the allocator's break (it never decreases), the free-list length and the free
bytes. Run it before and after a feature (an MTP session, a USB restart) to see whether
stock code keeps memory: growth of "used" per repetition is a leak.
"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import xyf_host                      # noqa: E402
from xy_forever import usb_proto as P   # noqa: E402

CONTEXT = 0x88a65bf8                 # target/cold_heap.hpp: arena_context


def peek(dev, address, n):
    code, body = dev.call(P.PEEK, struct.pack('<II', address, n))
    if code or len(body) != 4 + n:
        raise SystemExit('peek 0x%08x: %s' % (address, P.status_name(code)))
    return body[4:]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--timeout', type=float, default=15)
    args = ap.parse_args(argv)
    dev, _, _ = xyf_host.connect(args.timeout)
    base, extent, _, brk, _, _, head = struct.unpack('<7I', peek(dev, CONTEXT, 28))
    nodes = free = 0
    node = head
    while node and nodes < 4096:
        if not 0x88000000 <= node < 0x88b00000 - 12:
            print('free list leaves the PEEK range at 0x%08x after %d nodes' % (node, nodes))
            break
        size, _, node = struct.unpack('<3I', peek(dev, node, 12))
        nodes += 1
        free += size
    print('heap: break 0x%08x (%d bytes from base), free nodes %d, free %d, used %d'
          % (brk, brk - base, nodes, free, brk - base - free))
    return 0


if __name__ == '__main__':
    sys.exit(main())
