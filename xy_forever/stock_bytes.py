"""No teenage engineering bytes in a release image (#178): the runs of bytes an image shares with the stock pair.

XY Forever's product os.ldr names stock code by address and CRC and embeds no OS, so it must share no run of
MIN_RUN or more bytes with the stock 1.2.1 os.ldr or bootloader.ldr. Two kinds of shared bytes do not count:
- public tables both carry: the CRC-32 table (polynomial 0xEDB88320) and the SHA-256 round constants (FIPS 180-4),
  computed here and blanked in the image before the comparison;
- near-constant filler: a run is found through 32-byte anchors holding at least 8 different byte values, so long
  stretches of zeros or 0xff (padding both have) never start one.
`xyf_build.py --verify` requires shared_runs() == [] for the product (the mode with no embedded OS), and
`scripts/web_release.py` checks it again before it pins and stages an image for the web app.
"""

import struct

MIN_RUN = 64
ANCHOR = 32
DIVERSE = 8


def crc32_table():
    out = []
    for n in range(256):
        for _ in range(8):
            n = (n >> 1) ^ 0xEDB88320 if n & 1 else n >> 1
        out.append(n)
    return out


def sha256_constants():
    """The 64 round constants: the first 32 bits of the fractional parts of the cube roots of the first 64 primes."""
    primes, n = [], 2
    while len(primes) < 64:
        if all(n % p for p in primes if p * p <= n):
            primes.append(n)
        n += 1
    out = []
    for p in primes:
        lo, hi = 0, 1 << 40  # integer cube root of p << 96: 32 fraction bits
        target = p << 96
        while lo < hi:
            mid = (lo + hi + 1) // 2
            lo, hi = (mid, hi) if mid**3 <= target else (lo, mid - 1)
        out.append(lo & 0xFFFFFFFF)
    return out


def public_tables():
    """The public tables, in both byte orders."""
    tables = []
    for words in (crc32_table(), sha256_constants()):
        for order in '<>':
            tables.append(struct.pack('%s%dI' % (order, len(words)), *words))
    return tables


def blank_public(image):
    """image with every public table replaced by bytes no stock image holds in that place (a counting pattern XORed)."""
    out = bytearray(image)
    for table in public_tables():
        at = out.find(table)
        while at >= 0:
            out[at : at + len(table)] = bytes((i * 151 + 77) & 0xFF ^ 0x5A for i in range(len(table)))
            at = out.find(table, at + len(table))
    return bytes(out)


def shared_runs(image, stocks, min_run=MIN_RUN):
    """[(length, image offset, stock name, stock offset)] for each run of at least min_run bytes the image shares with a
    stock image (stocks: {name: bytes}), longest first; [] when there is none."""
    image = blank_public(image)
    found = {}
    for name, stock in stocks.items():
        anchors = {}
        for at in range(0, len(stock) - ANCHOR + 1, ANCHOR):
            window = stock[at : at + ANCHOR]
            if len(set(window)) >= DIVERSE:
                anchors.setdefault(window, []).append(at)
        i = 0
        while i <= len(image) - ANCHOR:
            spots = anchors.get(image[i : i + ANCHOR])
            if not spots:
                i += 1
                continue
            best = None
            for at in spots:
                start, s = i, at
                while start > 0 and s > 0 and image[start - 1] == stock[s - 1]:
                    start -= 1
                    s -= 1
                end, e = i + ANCHOR, at + ANCHOR
                while end < len(image) and e < len(stock) and image[end] == stock[e]:
                    end += 1
                    e += 1
                if best is None or end - start > best[0]:
                    best = (end - start, start, name, s)
            if best[0] >= min_run:
                found[(best[1], name)] = max(found.get((best[1], name), best), best)
            i += 1
    return sorted(found.values(), reverse=True)
