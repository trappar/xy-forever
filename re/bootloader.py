"""The stock 1.2.1 bootloader, offline: sparse memory image and annotated listing.

Inputs (private, never in git): LISTING_121/image121/{index.tsv,<start>.bin} and
LISTING_121/linear121.ann.asm (see private/xy-forever/analysis/bootloader-121-listing/FINDINGS.md).

    python3 re/bootloader.py ADDR... [-d DEPTH] [-a]   summarize functions (-a: full asm)
"""
import bisect
import re
import sys
from functools import lru_cache
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from xy_forever.paths import LISTING_121  # noqa: E402

# ---- sparse image (DumpBlocks export)
blocks = []
for _line in (LISTING_121 / 'image121/index.tsv').read_text().splitlines():
    _start, _size, _ = _line.split('\t')
    blocks.append((int(_start, 16), (LISTING_121 / f'image121/{_start}.bin').read_bytes()))
blocks.sort()
starts = [b[0] for b in blocks]


def read(a, n):
    i = bisect.bisect_right(starts, a) - 1
    if i < 0:
        return None
    s, d = blocks[i]
    return d[a - s:a - s + n] if a - s < len(d) else None


def u32(a):
    b = read(a, 4)
    return int.from_bytes(b, 'little') if b and len(b) == 4 else None


def cstr(a, minlen=3):
    b = read(a, 200)
    if not b:
        return None
    e = b.find(b'\0')
    if e < minlen:
        return None
    s = b[:e]
    return s.decode() if all(32 <= c < 127 or c in (9, 10) for c in s) else None


# ---- annotated listing
@lru_cache(maxsize=1)
def listing():
    lines = (LISTING_121 / 'linear121.ann.asm').read_text().splitlines()
    addr = [int(line.split('\t', 1)[0], 16) for line in lines]
    return lines, addr, {a: i for i, a in enumerate(addr)}


def body(a):
    lines, _, index = listing()
    i = index.get(a)
    if i is None:
        return []
    out = []
    for j in range(i, min(i + 3000, len(lines))):
        out.append(lines[j])
        if j > i and ('Linkage LINK' in lines[j + 1] if j + 1 < len(lines) else True) and \
           ('RTS' in lines[j] or 'JUMP' in lines[j] or 'RTI' in lines[j] or 'NOP' in lines[j]):
            break
    return out


def calls_of(b):
    calls = []
    for line in b:
        m = re.search(r'CALL(?:\.A)? 0x([0-9a-f]+)', line)
        if m and int(m.group(1), 16) not in calls:
            calls.append(int(m.group(1), 16))
    return calls


def summary(a, depth=0, seen=None, indent='', out=print):
    seen = set() if seen is None else seen
    if a in seen:
        out(f'{indent}{a:08x} (seen)'); return
    seen.add(a)
    b = body(a)
    if not b:
        out(f'{indent}{a:08x} (not in listing)'); return
    strs = [line.split('; ', 1)[1] for line in b if '; "' in line]
    calls = calls_of(b)
    mem = sorted({m for line in b for m in re.findall(r'\[(0x[0-9a-f]{8})\]', line)} |
                 {m for line in b if '; "' not in line
                  for m in re.findall(r'= (0x(?:88|80|11|20|1f|08)[0-9a-f]{6})\b', line)})
    out(f'{indent}{a:08x} len {len(b)} end {b[-1].split()[0]}  strs {strs[:6]}')
    if mem:
        out(f'{indent}   mem {" ".join(mem[:16])}')
    if calls:
        out(f'{indent}   calls {" ".join("%08x" % c for c in calls)}')
    if depth > 0:
        for c in calls:
            summary(c, depth - 1, seen, indent + '    ', out)


if __name__ == '__main__':
    args = sys.argv[1:]
    depth = int(args[args.index('-d') + 1]) if '-d' in args else 0
    for t in [int(x, 16) for x in args if re.fullmatch(r'[0-9a-fA-F]{8}', x)]:
        print('\n'.join(body(t))) if '-a' in args else summary(t, depth)
