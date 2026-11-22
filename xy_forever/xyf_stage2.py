"""Pack / inspect XY Forever stage-2 files (target/xyf_stage2.hpp). Offline."""

import hashlib
import struct
import zlib

MAGIC, ABI, HEADER_BYTES = 0x32535958, 1, 96
ABI_HELLO, ABI_MENU, ABI_SERVICE = 1, 2, 3  # hello test image; menu; the product's
WINDOW = (0x8AB90000, 0x8AC00000)  # target/xyf_stage2.hpp window_begin..window_end
PATH = '/yaffs2/xy-forever/stage2/main.xys2'


def pack(image, load_address, entry, version, build_id, min_stage1=1, abi=ABI):
    if abi not in (ABI_HELLO, ABI_MENU, ABI_SERVICE):
        raise ValueError('unknown ABI')
    if not image or len(image) > WINDOW[1] - WINDOW[0]:
        raise ValueError('image size outside the load window')
    if load_address < WINDOW[0] or load_address % 4 or load_address + len(image) > WINDOW[1]:
        raise ValueError('load range outside 0x%08x..0x%08x' % WINDOW)
    if not load_address <= entry < load_address + len(image) or entry % 2:
        raise ValueError('entry outside the image or misaligned')
    bid = build_id.encode('ascii')
    if len(bid) > 15:
        raise ValueError('build id longer than 15 bytes')
    head = struct.pack(
        '<8I32sII16sI',
        MAGIC,
        abi,
        HEADER_BYTES,
        0,
        load_address,
        entry,
        len(image),
        zlib.crc32(image),
        hashlib.sha256(image).digest(),
        min_stage1,
        version,
        bid,
        0,
    )
    return head + struct.pack('<I', zlib.crc32(head)) + image


def inspect(data):
    if len(data) < HEADER_BYTES:
        raise ValueError('too short')
    fields = struct.unpack_from('<8I32sII16sII', data)
    magic, abi, hb, flags, load, entry, size, crc, sha, min1, ver, bid, reserved, hcrc = fields
    if (
        magic != MAGIC
        or abi not in (ABI_HELLO, ABI_MENU, ABI_SERVICE)
        or hb != HEADER_BYTES
        or hcrc != zlib.crc32(data[:92])
        or flags
        or reserved
    ):
        raise ValueError('bad header')
    image = data[HEADER_BYTES:]
    if len(image) != size or zlib.crc32(image) != crc or hashlib.sha256(image).digest() != sha:
        raise ValueError('image does not match its header')
    return dict(
        abi=abi,
        load_address=hex(load),
        entry=hex(entry),
        bytes=size,
        version=ver,
        min_stage1=min1,
        build_id=bid.split(b'\0')[0].decode(),
        sha256=sha.hex(),
    )
