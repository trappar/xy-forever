"""target/xyf_official_content.hpp (#331): the official 1.2.1 paths the content set's job reads, and
op1_factory.json put back from Open-XY's builder output by taking out the entries it inserted. The always-run
cases are synthetic; with the private inputs, the table is checked against the 1.2.1 manifest, Open-XY's insertion
of every published machine (its kit's entries into the stock catalog, as builder/core.mjs inserts them) is undone
byte for byte, and so is an OP-XY's own op1_factory.json after an Open-XY install."""

import hashlib
import json
import re
import unittest

import xyf_hosttest

from xy_forever import paths

HEADER = paths.REPO / 'target/usb_service.hpp'
MANIFEST = paths.XYF / 'content-manifests/1.2.1/content.sha256'
KIT = paths.PUBLISHED_KIT
DEVICE = paths.FIXTURES / 'open-xy-op1_factory.json'  # an OP-XY's op1_factory.json after an Open-XY install
LISTS = [('engine_types', 1), ('fx_types', 1), ('seq_types', 2)]
# Shaped as the official file: each list's last entry closes on its own line, indented with spaces or a tab.
OFFICIAL = (
    b'{\n    "filter_types": [\n        {"type": "lp"}\n    ],\n'
    b'    "fx_types": [\n        {\n            "type": "reverb",\n            "id": 2\n        }\n    ],\n'
    b'    "seq_types": [\n        {"type": "arp", "id": 0},\n        {"type": "seq", "id": 1}\n\t],\n'
    b'    "engine_types": [\n        {\n            "type": "drum",\n            "default_params": [0, 1]\n'
    b'        }\n\t]\n}\n'
)
# Open-XY's catalog entries: a leading comma, some with a newline of their own at the end.
ENTRIES = {
    'engine_types': [
        ',\n        {\n            "type": "fm2op",\n            "x": "a]\\"}"\n        }\n',
        ',\n        {"type": "dust"}',
    ],
    'fx_types': [',\n        {\n            "type": "ringmod",\n            "id": 1\n        }'],
    'seq_types': [',\n        {"type": "chords", "id": 24}\n'],
}


def official_catalog_sha256():
    body = re.search(r'official_catalog_sha256\[32\] = \{([^}]*)\}', HEADER.read_text()).group(1)
    return bytes(int(x, 16) for x in re.findall(r'0x([0-9a-f]{2})', body)).hex()


def insert(catalog, inserts, entries):
    """Open-XY's builder (builder/core.mjs buildAssets): each list's entries as text at its insertion point."""
    for key, at in sorted(inserts.items(), key=lambda item: -item[1]):
        catalog = catalog[:at] + ''.join(entries.get(key, ())).encode() + catalog[at:]
    return catalog


def ends(catalog):
    """Each list's insertion point, as Open-XY's profile pins it: just after its last entry."""
    text = catalog.decode()
    return {key: re.compile(r'\}\s*\]').search(text, text.index('"%s"' % key)).start() + 1 for key, _ in LISTS}


class Restore(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.native = xyf_hosttest.module('official_content')

    def restore(self, text, lists=LISTS):
        return self.native.restore(text, lists)

    def test_open_xy_entries_come_out_byte_for_byte(self):
        """Kept: an OP-XY that had Open-XY builds mods only when this gives the official bytes."""
        at = ends(OFFICIAL)
        for chosen in ({}, ENTRIES, {'seq_types': ENTRIES['seq_types']}, {'engine_types': ENTRIES['engine_types']}):
            with self.subTest(chosen=sorted(chosen)):
                self.assertEqual(self.restore(insert(OFFICIAL, at, chosen)), OFFICIAL)

    def test_a_changed_official_entry_is_not_put_back(self):
        changed = insert(OFFICIAL, ends(OFFICIAL), ENTRIES).replace(b'"lp"', b'"hp"')
        self.assertNotEqual(self.restore(changed), OFFICIAL)

    def test_text_not_shaped_so_gives_nothing(self):
        for text in (
            b'',
            b'[]',
            OFFICIAL.replace(b'"fx_types"', b'"fx"'),  # a list missing
            OFFICIAL.replace(b',\n        {"type": "seq", "id": 1}', b''),  # fewer entries than the count
            OFFICIAL[:-4],  # cut short
            OFFICIAL.replace(b'"arp"', b'"arp'),  # an unterminated string
        ):
            with self.subTest(text=text[:40]):
                self.assertIsNone(self.restore(text))

    def test_the_firmware_lists_are_open_xy_s(self):
        """The firmware's counts are the official lengths of the three lists Open-XY appends to."""
        if not paths.CATALOG_121.is_file():
            self.skipTest('no private stock catalog')
        stock = paths.CATALOG_121.read_bytes()
        catalog = json.loads(stock)
        self.assertEqual(self.native.restore(stock), stock)  # as it is
        for key, count in (('engine_types', 13), ('fx_types', 6), ('seq_types', 3)):
            self.assertEqual(len(catalog[key]), count, key)
            self.assertIsNone(self.native.restore(stock, [(key, count + 1)]))

    def test_every_published_machine_comes_out(self):
        """Open-XY's own insertion points and entries (its published kit) into the stock catalog, undone."""
        if not (paths.CATALOG_121.is_file() and KIT.is_file()):
            self.skipTest('no private stock catalog or Open-XY kit')
        kit = json.loads(KIT.read_bytes())
        stock = paths.CATALOG_121.read_bytes()
        kinds = {'engine_types': ('synth', 'drum', 'sample'), 'fx_types': ('effect',), 'seq_types': ('player',)}
        entries = {
            k: [m['catalog'] for m in kit['machines'] if m['catalog'] and m['kind'] in v] for k, v in kinds.items()
        }
        self.assertTrue(all(entries.values()))
        built = insert(stock, kit['profile']['stock_catalog_inserts'], entries)
        self.assertEqual(json.loads(built)['engine_types'][:13], json.loads(stock)['engine_types'])
        self.assertEqual(self.native.restore(built), stock)

    def test_an_op_xy_after_open_xy(self):
        """The file an OP-XY had after an Open-XY install: put back, it is the official one."""
        if not DEVICE.is_file():
            self.skipTest('no private Open-XY op1_factory.json')
        restored = self.native.restore(DEVICE.read_bytes())
        self.assertEqual(hashlib.sha256(restored).hexdigest(), official_catalog_sha256())


class Paths(unittest.TestCase):
    def test_the_table_is_the_official_list(self):
        if not MANIFEST.is_file():
            self.skipTest('no private 1.2.1 content manifest')
        native = xyf_hosttest.module('official_content')
        listed = [line.split('  ', 1)[1] for line in MANIFEST.read_text().splitlines()]
        want = sorted(int.from_bytes(hashlib.sha256(p.encode()).digest()[:4], 'big') for p in listed)
        self.assertEqual(native.path_hashes(), want)
        self.assertTrue(all(native.listed(p) for p in listed))
        self.assertFalse(native.listed('display/fm2op.svg'))
