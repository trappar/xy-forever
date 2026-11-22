"""The outer os.ldr stream: ROM trampoline, cold module and (except --xyf-product) the embedded stock payload.

The stock 1.2.1 SPI bootloader loads this stream from the NAND boot partition.
"""

import struct

from . import build_formats as formats
from .build_formats import Firmware
from .ldr import parse_ldr

BOOT_HASH = '139459f2a8b7bbe5cd9432d4870a758f4e9e9d7e875ebb10701144b73f40f75d'
HELPERS = ((0x88A346A6, 0x176), (0x8803D000, 0x2A), (0x0803E0C0, 0x82))
ALLOWED = ((0x08000000, 0x08040000), (0x11800000, 0x11C00000), (0x80000000, 0x90000000))
PROTECTED = (
    (0x0803E0C0, 0x0803E142),
    (0x11803000, 0x11804000),
    (0x8803D000, 0x8803D02A),
    (0x88A346A6, 0x88A3481C),
    (0x88A34AEC, 0x88A34D6C),
    (0x8A000000, 0x8A810000),
    (0x8B000000, 0x8B800000),
)


def module_trampoline(address):
    if not 0 <= address <= 0xFFFFFFFF or address & 1:
        raise ValueError('Invalid module entry')
    return struct.pack('<4H', 0xDC00, address >> 16, address & 65535, 0)


def records(module, payload, module_base):
    loads = [(0x11A00000, module_trampoline(module_base)), (module_base, module)]
    return loads + ([(0x8B000000, payload)] if payload is not None else [])  # None: no embedded OS


def package(module, payload, module_base=0x8A800000):
    body = b''.join(
        formats.header(4, address, len(data), 0) + data for address, data in records(module, payload, module_base)
    )
    body += formats.header(0x8004, 0x11A00000, 0, 0)
    return formats.header(0x5004, 0x11A00000, 0, len(body)) + body


def verify_outer(candidate, module, payload, module_base=0x8A800000):
    parsed = parse_ldr(candidate)
    expected = records(module, payload, module_base)
    if len(parsed) != len(expected) + 2 or parsed[-1].target != 0x11A00000:
        raise ValueError('Unexpected outer stream layout')
    if candidate[:16] != formats.header(0x5004, 0x11A00000, 0, len(candidate) - 16):
        raise ValueError('Unexpected outer FIRST record')
    mapped = Firmware(candidate)
    for r, (address, data) in zip(parsed[1:-1], expected):
        if r.flags != 4 or r.target != address or r.count != len(data) or mapped.read(address, len(data)) != data:
            raise ValueError('Independent outer-image reconstruction differs')
    return dict(
        independent_outer_mapping=True,
        exact_embedded_payload=payload is not None,
        embedded_os=payload is not None,
        records=len(parsed),
        no_bootloader_component=True,
        hardware_tested=False,
    )
