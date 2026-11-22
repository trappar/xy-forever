"""Merge reachable 1.2.1 functions into CRC spans for target/*_spans.hpp.

    python3 re/spans.py REACH.txt... > spans.txt

REACH files come from re/reach.py or re/mtp_reach.py. Words that differ from the
image in any cold census report (REPORTS/test-0*/**/*.bin, Tests 017-024) are
excluded, because they change at run time. Prints C++ table rows; the build
re-checks every CRC against the authenticated bootloader.
"""

import glob
import sys
import zlib
from pathlib import Path

import bootloader as img

from xy_forever import census
from xy_forever.paths import REPORTS


def pieces_of(a, e):
    """Split [a, e) at image block boundaries; skip addresses outside loaded blocks."""
    out = []
    while a < e:
        i = img.bisect.bisect_right(img.starts, a) - 1
        if i < 0:
            a += 4
            continue
        s, d = img.blocks[i]
        if a - s >= len(d):
            nxt = img.starts[i + 1] if i + 1 < len(img.starts) else e
            a = max(a + 4, nxt)
            continue
        end = min(e, s + len(d))
        out.append((a, end))
        a = end
    return out


def main(files):
    ranges = []
    for f in files:
        for line in open(f):
            p = line.split()
            if len(p) == 2 and len(p[0]) == 8 and p[0] != 'external':
                a, e = int(p[0], 16), int(p[1], 16) + 2
                if (
                    (0x889E0000 <= a < 0x88A36000 or 0x0803C000 <= a < 0x08040000)
                    and e - a < 0x3000
                    and e <= (0x88A36000 if a >= 0x889E0000 else 0x08040000)
                ):
                    ranges.append((a, e))
    ranges.sort()
    merged = []
    for a, e in ranges:
        if merged and a <= merged[-1][1] + 0x40:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([a, e])
    dumps = []
    for c in sorted(glob.glob(str(REPORTS / 'test-0*' / '**' / '*.bin'), recursive=True)):
        try:
            _, hdr, regions, dump = census.parse(open(c, 'rb').read())
            dumps.append((c, hdr['role'], regions, dump))
        except Exception:
            pass
    # Cut runs of >= 16 zero bytes (data/BSS holes inside mis-sized "functions").
    pieces, blocked = [], []
    for a, e in merged:
        blocked += pieces_of(a & ~3, (e + 3) & ~3)
    for a, e in blocked:
        data = img.read(a, e - a)
        start, i = a, 0
        while i < len(data):
            if data[i : i + 16] == bytes(16):
                j = i
                while j < len(data) and data[j] == 0:
                    j += 1
                cut = (a + i) & ~3
                if cut > start:
                    pieces.append((start, cut))
                start = (a + j + 3) & ~3
                i = j
            else:
                i += 4
        if e > start:
            pieces.append((start, e))
    spans = []
    for a, e in pieces:
        image = img.read(a, e - a)
        bad, covered = set(), 0
        for _, _, regions, dump in dumps:
            live = census.read(regions, dump, a, e - a)
            if live is None:
                continue
            covered += 1
            for i in range(0, e - a, 4):
                if live[i : i + 4] != image[i : i + 4]:
                    bad.add(a + i)
        start = a  # split around mutable words
        for w in sorted(bad) + [e]:
            if w > start:
                spans.append((start, w - start, zlib.crc32(img.read(start, w - start)), covered))
            start = w + 4
        if bad:
            print('mutable words', [hex(x) for x in sorted(bad)][:8], file=sys.stderr)
    for s in spans:
        print('    {0x%08xu, 0x%xu, 0x%08xu},   // census-covered %d' % s)
    print('total bytes', sum(s[1] for s in spans), 'spans', len(spans), 'census files', len(dumps), file=sys.stderr)


if __name__ == '__main__':
    main(sys.argv[1:])
