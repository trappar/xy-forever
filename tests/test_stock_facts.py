"""stock_facts.json (#303): the facts agree with the other pins, the build reads them, and with the private stock pair
present every fact is recomputed and a changed one fails."""

import copy
import json
import unittest

from xy_forever import probe_report, stock_facts, usb_proto
from xy_forever import xyf_build as B
from xy_forever.paths import REPO, STOCK_121

FACTS = stock_facts.load()


class FactsTest(unittest.TestCase):
    def test_hashes_are_the_stock_pins(self):
        pins = json.loads((REPO / 'stock.json').read_text())['stock']
        for version, files in pins.items():
            for name, component in (('os.ldr', 'os'), ('bootloader.ldr', 'bootloader')):
                if version in FACTS[component]:
                    self.assertEqual(FACTS[component][version]['sha256'], files[name])
        os_facts = FACTS['os'][stock_facts.BASE]
        self.assertEqual(
            (usb_proto.STOCK_OS_LDR_BYTES, usb_proto.STOCK_OS_LDR_SHA256), (os_facts['bytes'], os_facts['sha256'])
        )
        self.assertEqual(probe_report.STOCK121_OS[0], os_facts['bytes'])

    def test_the_build_checks_read_the_facts(self):
        boot = stock_facts.Bootloader()
        B.check_boot_reservations(boot)
        B.check_os_facts()
        B.check_construct_spans(boot)
        self.assertEqual(len(B.descriptor_words(boot, b'')), 7)
        with self.assertRaisesRegex(ValueError, 'lacks bootloader 1.2.1 0x00000000\\+0x4$'):
            boot.crc(0, 4)


class RecomputeTest(unittest.TestCase):
    def setUp(self):
        files = [STOCK_121 / n for n in ('os.ldr', 'bootloader.ldr')]
        if not all(f.is_file() for f in files):
            self.skipTest('the private stock 1.2.1 pair is absent (setup.py import-stock)')
        self.stock, self.boot = (f.read_bytes() for f in files)

    def test_every_fact_matches_the_stock_pair(self):
        boot = stock_facts.recompute(FACTS, self.stock, self.boot)
        with self.assertRaisesRegex(ValueError, 'CRC-32 0x[0-9a-f]{8}$'):  # a missing range: the value to add
            boot.crc(0x0803C000, 4)

    def test_a_changed_fact_fails(self):
        for change in (
            lambda f: f['bootloader']['1.2.1']['crc32'].update({'0x88a346a6+0x176': '0x00000000'}),
            lambda f: f['bootloader']['1.2.1']['loaded'].pop(),
            lambda f: f['os']['1.2.1'].update(bytes=1),
            lambda f: f['os']['1.2.1'].update(sha256='0' * 64),
        ):
            facts = copy.deepcopy(FACTS)
            change(facts)
            with self.assertRaises(ValueError):
                stock_facts.recompute(facts, self.stock, self.boot)


if __name__ == '__main__':
    unittest.main()
