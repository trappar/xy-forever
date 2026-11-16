#!/usr/bin/env python3
"""Compare stock code in the device's RAM with the authenticated 1.2.1 image, through
XY Forever's PEEK (host only, read-only).

usage: python3 scripts/spandiff.py [--table mtp|usb] [--span N] [--timeout S]

Reads every CRC span of target/mtp_spans.hpp (or usb_spans.hpp) that PEEK may read and
prints each 32-bit word that differs from the image. A word that differs is live data
inside a checked span: it must be excluded from the span table (re/spans.py), or the
"stock code check" refuses the feature once that word has changed.
"""
import argparse
import os
import re
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 're'))
import xyf_host                      # noqa: E402
from xy_forever import usb_proto as P   # noqa: E402
import bootloader                    # noqa: E402


def spans(table):
    rows = []
    for line in open(os.path.join(ROOT, 'target', table + '_spans.hpp')):
        m = re.match(r'\s*\{0x([0-9a-f]+)u, 0x([0-9a-f]+)u, 0x([0-9a-f]+)u\}', line)
        if m:
            rows.append((int(m.group(1), 16), int(m.group(2), 16)))
    return rows


def peekable(a, n):
    return 0x88000000 <= a and a + n <= 0x88b00000 or 0x08000000 <= a and a + n <= 0x08040000


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--table', default='mtp', choices=('mtp', 'usb'))
    ap.add_argument('--span', type=int)
    ap.add_argument('--timeout', type=float, default=15)
    args = ap.parse_args(argv)
    dev, _, _ = xyf_host.connect(args.timeout)
    rows = spans(args.table)
    differing = skipped = 0
    for i, (a, n) in enumerate(rows):
        if args.span is not None and i != args.span:
            continue
        if not peekable(a, n):
            skipped += 1
            continue
        image = bootloader.read(a, n)
        live = bytearray()
        while len(live) < n:
            k = min(1024, n - len(live))
            code, body = dev.call(P.PEEK, struct.pack('<II', a + len(live), k))
            if code or len(body) != 4 + k:
                raise SystemExit('peek 0x%08x: %s' % (a + len(live), P.status_name(code)))
            live += body[4:]
        for at in range(0, n - n % 4, 4):
            if live[at:at + 4] != image[at:at + 4]:
                differing += 1
                print('span %d (0x%08x + 0x%x): 0x%08x image %s device %s'
                      % (i, a, n, a + at, image[at:at + 4].hex(), bytes(live[at:at + 4]).hex()))
        if n % 4 and live[n - n % 4:] != image[n - n % 4:n]:
            differing += 1
            print('span %d: tail bytes differ at 0x%08x' % (i, a + n - n % 4))
    print('%d spans, %d not readable over PEEK, %d differing words' % (len(rows), skipped, differing))
    return 1 if differing else 0


if __name__ == '__main__':
    sys.exit(main())
