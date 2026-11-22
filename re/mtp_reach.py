"""Reachability from stock MTP code (1.2.1 bootloader listing) and the forbidden-target check.

    uv run re/mtp_reach.py

Function starts: LINK instructions, CALL targets, and the first line after an address gap.
"""

import bisect
import re
from collections import deque

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
        if 0x889F1BF0 <= addrs[s] < 0x889F2800
        or 0x88A08700 <= addrs[s] < 0x88A14000
        or 0x889F2800 <= addrs[s] < 0x889F9000
    ]
    + [0x88A34FEC, 0x88A35044]
    + [0x88A1247A, 0x88A1D69E, 0x88A1D72C]
)
bad = {
    0x889F6E5A: 'sector write 0x15',
    0x889F6EAC: 'sector write 0x16',
    0x889F683C: 'select 0x18',
    0x889F68D4: 'trim 0x14',
    0x88A06A7E: 'mmc_tools writer',
    0x11A062C6: 'spi flash',
    0x11A06376: 'spi flash',
    0x11A06470: 'spi flash',
    0x11A05312: 'spi meta',
    0x88A2863C: 'updater',
    0x88A2775E: 'updater',
    0x88A2830E: 'updater',
    0x88A35668: 'bootloader main',
    0x88A2742C: 'companion',
    0x88A2756E: 'companion',
    0x88A26F90: 'companion',
    0x88A26A80: 'companion',
    0x88A27278: 'companion',
}
prev = dict.fromkeys(roots)
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
