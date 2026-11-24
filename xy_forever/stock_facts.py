"""The stock facts the firmware build checks against (stock_facts.json, #303): never the stock bytes.

stock_facts.json holds, by component and version, what the build reads from the stock files: for the bootloader,
the CRC-32 of every range a target/ span table, the cold descriptor (outer.HELPERS) or the memory-map audit names,
its statically loaded ranges, size and SHA-256; for the OS, its size and SHA-256. The product build needs nothing
else, so it runs without the stock files (CI). With them present (a contributor's machine, `mise run check`),
recompute() first derives every fact of the base from the files and refuses any difference.

A range the build checks that the file lacks fails the build; with the stock files present the message gives its
CRC-32 to add. A later OS base (#188) is one more entry under "os"; the bootloader stays 1.2.1 (docs/SAFETY.md).
"""

import json
import zlib

from .build_formats import Firmware, require
from .compile import sha
from .paths import REPO

PATH = REPO / 'stock_facts.json'
BASE = '1.2.1'  # the product's OS base and the only bootloader XY Forever runs on


def load(path=PATH):
    return json.loads(path.read_text())


def span_key(address, length):
    return '0x%08x+0x%x' % (address, length)


def loaded_ranges(image):
    """A Firmware's statically loaded memory as merged [low, high) ranges."""
    out = []
    for lo, hi, _ in image.segments:
        if out and out[-1][1] == lo:
            out[-1] = (out[-1][0], hi)
        else:
            out.append((lo, hi))
    return out


class Bootloader:
    """One bootloader version's facts; image (a Firmware of the stock file) only names a missing range's CRC."""

    def __init__(self, facts=None, version=BASE, image=None):
        self.version = version
        self.facts = (facts or load())['bootloader'][version]
        self.image = image

    def crc(self, address, length):
        value = self.facts['crc32'].get(span_key(address, length))
        if value is None:
            known = ', CRC-32 0x%08x' % zlib.crc32(self.image.read(address, length)) if self.image else ''
            raise ValueError(
                'stock_facts.json lacks bootloader %s %s%s' % (self.version, span_key(address, length), known)
            )
        return int(value, 16)

    def loaded(self):
        return [(int(lo, 16), int(hi, 16)) for lo, hi in self.facts['loaded']]


def recompute(facts, stock, boot, version=BASE):
    """Every fact of a base from the stock pair's bytes; a difference fails. Returns the Bootloader."""
    os_facts, boot_facts = facts['os'][version], facts['bootloader'][version]
    require(
        sha(stock) == os_facts['sha256'] and sha(boot) == boot_facts['sha256'],
        'Exact stock %s inputs required' % version,
    )
    require(len(stock) == os_facts['bytes'] and len(boot) == boot_facts['bytes'], 'stock_facts.json sizes differ')
    image = Firmware(boot)
    for key, value in boot_facts['crc32'].items():
        address, length = (int(n, 16) for n in key.split('+'))
        require(
            zlib.crc32(image.read(address, length)) == int(value, 16),
            'stock_facts.json bootloader %s %s differs from the stock file' % (version, key),
        )
    require(
        [['0x%08x' % lo, '0x%08x' % hi] for lo, hi in loaded_ranges(image)] == boot_facts['loaded'],
        'stock_facts.json bootloader %s loaded ranges differ from the stock file' % version,
    )
    return Bootloader(facts, version, image)
