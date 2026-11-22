"""Reachability from stock MTP code (1.2.1 bootloader listing) and the forbidden-target check.

    python3 re/mtp_reach.py

Function starts: LINK instructions, CALL targets, and the first line after an address gap.
"""
import bisect
from collections import deque
from pathlib import Path
import re

from xy_forever.paths import LISTING_121

lines = (LISTING_121 / 'linear121.ann.asm').read_text().splitlines()
addrs = [int(l.split('\t', 1)[0], 16) for l in lines]
idx = {a: i for i, a in enumerate(addrs)}
starts = {0}
calls_raw = []
for i, l in enumerate(lines):
    if re.search(r'\bLINK\b', l) and 'UNLINK' not in l:
        starts.add(i)
    if i and addrs[i] - addrs[i - 1] > 0x40:
        starts.add(i)
    m = re.search(r'CALL(?:\.A)? 0x([0-9a-f]+)', l)
    if m:
        t = int(m.group(1), 16)
        calls_raw.append((i, t))
        if t in idx:
            starts.add(idx[t])
starts = sorted(starts)
g = {}
for i, t in calls_raw:
    k = bisect.bisect_right(starts, i) - 1
    g.setdefault(addrs[starts[k]], set()).add(t)
roots = (
    [
        addrs[s]
        for s in starts
        if 0x889f1bf0 <= addrs[s] < 0x889f2800
        or 0x88a08700 <= addrs[s] < 0x88a14000
        or 0x889f2800 <= addrs[s] < 0x889f9000
    ]
    + [0x88a34fec, 0x88a35044]
    + [0x88a1247a, 0x88a1d69e, 0x88a1d72c]
)
bad = {
    0x889f6e5a: 'sector write 0x15',
    0x889f6eac: 'sector write 0x16',
    0x889f683c: 'select 0x18',
    0x889f68d4: 'trim 0x14',
    0x88a06a7e: 'mmc_tools writer',
    0x11a062c6: 'spi flash',
    0x11a06376: 'spi flash',
    0x11a06470: 'spi flash',
    0x11a05312: 'spi meta',
    0x88a2863c: 'updater',
    0x88a2775e: 'updater',
    0x88a2830e: 'updater',
    0x88a35668: 'bootloader main',
    0x88a2742c: 'companion',
    0x88a2756e: 'companion',
    0x88a26f90: 'companion',
    0x88a26a80: 'companion',
    0x88a27278: 'companion',
}
prev = {r: None for r in roots}
q = deque(roots)
while q:
    a = q.popleft()
    for c in g.get(a, []):
        if c not in prev:
            prev[c] = a
            q.append(c)
print(len(prev), 'functions reachable by direct calls from', len(roots), 'roots')
hits = 0
for b, n in bad.items():
    if b in prev:
        hits += 1
        p = []
        a = b
        while a is not None:
            p.append(hex(a))
            a = prev[a]
        print('REACHES', n, ' <- '.join(p))
print('forbidden reached:', hits)
