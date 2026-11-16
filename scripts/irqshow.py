#!/usr/bin/env python3
"""Decode B's interrupt state from PEEK dumps (read-only, host only).

usage: python3 scripts/irqshow.py DIR
DIR holds xyf_host.py peek dumps: sctl.bin (SEC SCTL/SSTAT, sources 0..0x4f:
peek 0x20004800 640) and optionally l1.bin (L1 data: peek 0x11b00000 8192),
imask.bin (IMASK/IPEND/ILAT: peek 0x1fc02104 12), gctl.bin (peek 0x20004000 8),
fctl.bin (peek 0x20004010 8) and cctl.bin (peek 0x20004400 8).

Stock tables (1.2.1 bootloader, see xy-forever-mtp-001/FINDINGS.md):
  B[0x11b00ac4 + source]   slot of a registered SEC source (88a33d44)
  0x11b005d0 + slot * 8     {entry, argument} called by the IVG handler 11a01570
  0x11b008d0 + slot * 4     the real handler behind the nesting wrapper 11a015d8
SCTL fields as the stock setters use them: bit 0 interrupt enable (88a31a5c),
bit 2 source enable (88a31a34), bits 8..15 priority (88a319fa), bits 24..27 core
target group (88a319c0).
"""
import struct
import sys
from pathlib import Path

NAMES = {0x0b: 'stock prio 4 (USB start)', 0x14: 'PINT (link)', 0x15: 'PINT (link)',
         0x24: 'SPI0', 0x25: 'SPI0', 0x26: 'SPI0', 0x27: 'SPI0 DMA',
         0x2c: 'SPI1 (slave)', 0x2d: 'SPI1 (slave)', 0x2e: 'SPI1', 0x2f: 'SPI1 DMA',
         0x3b: 'display', 0x48: 'stock prio 1 (USB start)', 0x4d: 'USB', 0x4e: 'USB DMA'}
L1 = 0x11b00000


def words(path):
    data = path.read_bytes()
    return struct.unpack('<%dI' % (len(data) // 4), data)


def main(directory):
    d = Path(directory)
    for name, base, labels in (('gctl.bin', 0x20004000, ('GCTL', 'GSTAT')), ('fctl.bin', 0x20004010, ('FCTL', 'FSTAT')),
                               ('cctl.bin', 0x20004400, ('CCTL0', 'CSTAT0')),
                               ('imask.bin', 0x1fc02104, ('IMASK', 'IPEND', 'ILAT'))):
        if (d / name).exists():
            print('  '.join('%s=0x%08x' % (l, v) for l, v in zip(labels, words(d / name))))
    l1 = (d / 'l1.bin').read_bytes() if (d / 'l1.bin').exists() else b''
    def l1word(a):
        o = a - L1
        return struct.unpack_from('<I', l1, o)[0] if 0 <= o <= len(l1) - 4 else None
    sctl = words(d / 'sctl.bin')
    print('src  SCTL       IEN SEN prio ctg  SSTAT      slot entry      arg        handler   name')
    for source in range(len(sctl) // 2):
        control, status = sctl[2 * source], sctl[2 * source + 1]
        slot = l1[0xac4 + source] if len(l1) > 0xac4 + source else None
        entry = arg = handler = None
        if slot not in (None, 0xff):
            entry, arg, handler = l1word(0x11b005d0 + slot * 8), l1word(0x11b005d4 + slot * 8), l1word(0x11b008d0 + slot * 4)
        if not (control & 5) and source not in NAMES:
            continue
        fmt = lambda v: '-' if v is None else '0x%08x' % v
        print('0x%02x 0x%08x  %d   %d  %3d  %2d   0x%08x %4s %s %s %s %s' % (
            source, control, control & 1, control >> 2 & 1, control >> 8 & 0xff, control >> 24 & 0xf, status,
            '-' if slot is None else slot, fmt(entry), fmt(arg), fmt(handler), NAMES.get(source, '')))


if __name__ == '__main__':
    if len(sys.argv) != 2:
        print(__doc__); sys.exit(2)
    main(sys.argv[1])
