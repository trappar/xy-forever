"""The stock facts the product build needs (stock_facts.json, #303): never the stock bytes.

stock_facts.json holds, by component and version, the stock files' identities (size, SHA-256) and the only
stock-derived values that reach the product image: the CRC-32s of the bootloader helpers the cold descriptor names
(outer.HELPERS; target/cold.cpp checks them on the device). The product build reads them from here, so it runs
without the stock files (CI). With them present (a contributor's machine, `mise run check`), recompute() first
derives every fact of the base from the files and refuses any difference, and the build's other checks against the
stock bootloader (the span tables in target/, its loaded memory) run as well; without them those are skipped.

A later OS base (#188) is one more entry under "os"; the bootloader stays 1.2.1 (docs/SAFETY.md).
"""

import json
import zlib

from .build_formats import Firmware, require
from .compile import sha
from .outer import HELPERS
from .paths import REPO

PATH = REPO / 'stock_facts.json'
BASE = '1.2.1'  # the product's OS base and the only bootloader XY Forever runs on


def load(path=PATH):
    return json.loads(path.read_text())


def helper_crcs(facts=None, version=BASE):
    """The CRC-32s of outer.HELPERS, in order, from the bootloader's facts."""
    helpers = (facts or load())['bootloader'][version]['helpers']
    require(
        [(int(h['address'], 16), int(h['length'], 16)) for h in helpers] == list(HELPERS),
        'stock_facts.json helpers differ from outer.HELPERS',
    )
    return [int(h['crc32'], 16) for h in helpers]


def recompute(facts, stock, boot, version=BASE):
    """Every fact of a base from the stock pair's bytes; a difference fails."""
    os_facts, boot_facts = facts['os'][version], facts['bootloader'][version]
    require(
        sha(stock) == os_facts['sha256'] and sha(boot) == boot_facts['sha256'],
        'Exact stock %s inputs required' % version,
    )
    require(len(stock) == os_facts['bytes'] and len(boot) == boot_facts['bytes'], 'stock_facts.json sizes differ')
    image = Firmware(boot)
    require(
        helper_crcs(facts, version) == [zlib.crc32(image.read(a, n)) for a, n in HELPERS],
        'stock_facts.json bootloader %s helper CRCs differ from the stock file' % version,
    )
