"""Stage-2 image audits for xyf_build.py: the freestanding image at its window, and what its code and data
may address."""

import re
import struct

from . import xyf_stage2 as S2
from .build_formats import elf32, require

# Stage 2 (--xyf-stage2): the hello image and its load window (target/xyf_stage2.hpp). Above the
# wide stack with a 48 KiB gap; the product's loader stays dormant (no file).
STAGE2_WINDOW = (0x8AB90000, 0x8AC00000)
STAGE2_ENTER = 'xyf_stage2_enter'  # the one register jump stage 1 may make (target/xyf_stage2_enter.s)
# The standalone modes' data audit (--xyf-stage2, --xyf-stage2-menu; #97). Their link brackets the compiler's
# merged-string sections (.rodata*.str1.*) with these symbols, and a second link with -q (stage2.audit.elf)
# keeps the data relocations. Literal data beyond those strings: symbol -> its exact size in bytes.
STAGE2_LITERALS = ('__stage2_literals', '__stage2_literals_end')
STAGE2_LITERAL_SYMBOLS = {'__ZN3xyf3gfx12_GLOBAL__N_1L4fontE': 475}  # ui_gfx.cpp's font[95][5]
R_BFIN_BYTE4_DATA = 0x12


def stage2_enter_sites(listing):
    """Register calls/jumps inside the stage-2 trampoline (must be exactly one)."""
    count, function = 0, None
    for line in listing.splitlines():
        named = re.match(r'^[0-9a-f]{8} <(.+)>:$', line)
        if named:
            function = named.group(1)
        elif function == STAGE2_ENTER and re.search(r'(?:call|jump) \((p[0-5])\)', line):
            count += 1
    return count


def foreign_ranges(image_bytes):
    """Where stage 2 may not point: stock code/data, L1, MMIO, core MMRs, SDRAM outside the image
    (above it, rounded up to 64 KiB, as a high-half load can only express)."""
    return (
        (0x08000000, 0x08100000),
        (0x11800000, 0x11C00000),
        (0x20000000, 0x30000000),
        (0x80000000, STAGE2_WINDOW[0]),
        ((STAGE2_WINDOW[0] + image_bytes + 0xFFFF) & ~0xFFFF, 0x90000000),
        (0xFFC00000, 0xFFFF0000),
    )


def scan_words(sections, foreign, literals=()):
    """No foreign address in any aligned word of .rodata and .data, except words wholly inside literals."""
    for name in ('.rodata', '.data'):
        section = sections.get(name)
        if not section or not section['data']:
            continue
        data = section['data']
        for at in range(0, len(data) - 3, 4):
            address = section['address'] + at
            if all(any(lo <= a < hi for lo, hi in literals) for a in range(address, address + 4)):
                continue
            value = struct.unpack_from('<I', data, at)[0]
            require(
                not any(lo <= value < hi for lo, hi in foreign),
                'Stage 2 data holds a foreign address %#x at %#x' % (value, address),
            )


def literal_ranges(symbols, sections):
    """The proven-literal ranges: the bracketed merged strings and STAGE2_LITERAL_SYMBOLS (exact sizes)."""
    rodata = sections.get('.rodata')
    require(rodata and all(name in symbols for name in STAGE2_LITERALS), 'Stage 2 link lacks its literal brackets')
    begin, end = (symbols[name]['value'] for name in STAGE2_LITERALS)
    ranges = [(begin, end)]
    for name, size in STAGE2_LITERAL_SYMBOLS.items():
        if name in symbols:
            require(symbols[name]['size'] == size, 'Stage 2 literal %s is not %d bytes' % (name, size))
            ranges.append((symbols[name]['value'], symbols[name]['value'] + size))
    for lo, hi in ranges:
        require(
            rodata['address'] <= lo <= hi <= rodata['address'] + len(rodata['data']),
            'Stage 2 literal range %#x..%#x outside .rodata' % (lo, hi),
        )
    return ranges


def audit_stage2_data(sections, relocations, literals, image_bytes):
    """Standalone stage-2 data (#97). Pass 1: every data relocation is R_BFIN_BYTE4_DATA to inside the image
    and lies outside the literal ranges. Pass 2: the aligned-word scan over everything but the literals."""
    begin = STAGE2_WINDOW[0]
    for name, entries in relocations.items():
        if name == '.text':
            continue  # code: the listing checks in check_stage2_image
        require(name in ('.rodata', '.data'), 'Stage 2 relocates data in ' + name)
        section = sections[name]
        for r in entries:
            at = r['offset'] - section['address']
            require(
                r['type'] == R_BFIN_BYTE4_DATA
                and 0 <= at <= len(section['data']) - 4
                and struct.unpack_from('<I', section['data'], at)[0] == r['target'],
                'Stage 2 data relocation at %#x is not a 4-byte pointer' % r['offset'],
            )
            require(
                begin <= r['target'] < begin + image_bytes,
                'Stage 2 data points outside itself: %#x at %#x' % (r['target'], r['offset']),
            )
            require(
                not any(lo < r['offset'] + 4 and r['offset'] < hi for lo, hi in literals),
                'Stage 2 relocation inside a literal range at %#x' % r['offset'],
            )
    scan_words(sections, foreign_ranges(image_bytes), literals)


def audit_relocated(out, image):
    """The -q relink (stage2.audit.elf) loads exactly stage2.bin; audit its data relocations and words."""
    sections, symbols, relocations = elf32((out / 'stage2.audit.elf').read_bytes(), relocations=True)
    loaded = bytearray(len(image))
    for section in sections.values():
        if section['flags'] & 2 and section['type'] != 8 and section['size']:
            at = section['address'] - STAGE2_WINDOW[0]
            require(0 <= at <= len(image) - section['size'], 'Stage 2 audit link differs from its image')
            loaded[at : at + section['size']] = section['data']
    require(bytes(loaded) == image, 'Stage 2 audit link differs from its image')
    audit_stage2_data(sections, relocations, literal_ranges(symbols, sections), len(image))


def check_stage2_image(out, relocated=False):
    """Stage 2: freestanding image at the window, packed file consistent. relocated: a standalone mode's
    image, whose data is audited by audit_relocated (the product's stage 2 keeps the plain word scan)."""
    sections, symbols = elf32((out / 'stage2.elf').read_bytes())
    image = (out / 'stage2.bin').read_bytes()
    require(not sections.get('.bss', {}).get('size', 0), 'Stage 2 has BSS')
    require(symbols['xyf_stage2_main']['value'] == STAGE2_WINDOW[0], 'Stage 2 entry is not at the window start')
    require(STAGE2_WINDOW[0] + len(image) <= STAGE2_WINDOW[1], 'Stage 2 exceeds its window')
    packed = (out / 'main.xys2').read_bytes()
    info = S2.inspect(packed)
    require(
        packed[S2.HEADER_BYTES :] == image
        and int(info['load_address'], 16) == STAGE2_WINDOW[0]
        and int(info['entry'], 16) == STAGE2_WINDOW[0],
        'Packed stage 2 differs from its image',
    )
    listing = (out / 'stage2.asm').read_text().lower()
    # Only its own code and data: no stock entry or other absolute address loads,
    # no direct calls outside the image; register calls are the ABI services.
    for m in re.finditer(r'(?:call|jump(?:\.[sl])?) 0x([0-9a-f]+)', listing):
        target = int(m.group(1), 16)
        require(
            STAGE2_WINDOW[0] <= target < STAGE2_WINDOW[0] + len(image), 'Stage 2 calls outside itself: ' + hex(target)
        )
    # Addresses are built with a high-half load (objdump's combined comment is
    # a heuristic, so judge the high half): none may point at stock code/data,
    # L1, MMIO, core MMRs or SDRAM outside the image. Small values (0x1 << 16)
    # are plain constants.
    foreign = foreign_ranges(len(image))
    for m in re.finditer(r'\b[a-z][a-z0-9]*\.h = 0x([0-9a-f]{1,4});', listing):
        high = int(m.group(1), 16) << 16
        require(not any(lo <= high < hi for lo, hi in foreign), 'Stage 2 builds a foreign address ' + hex(high))
    # No such address in its data either (every aligned word of .rodata and .data).
    if relocated:
        audit_relocated(out, image)
    else:
        scan_words(sections, foreign)
    return info
