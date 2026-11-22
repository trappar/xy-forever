import unittest

import xyf_hosttest

from xy_forever.packages import destination_for

ROLES = ('os', 'backplane', 'ble', 'metadata', 'content', 'excluded-bootloader', 'excluded-host-metadata')


class NativePolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.native = xyf_hosttest.module('policy')

    def classify(self, name):
        status, role, path = self.native.destination(name.encode('utf-8'))
        return status, ROLES[role], path.decode('ascii') or None

    def test_host_and_native_component_rules_agree(self):
        paths = [
            'firmware/blackfin/bin/os.ldr',
            'firmware/backplane/bin/backplane.cyacd',
            'firmware/ble/bin/ble.bin',
            'firmware/blackfin/bin/os.version',
            'firmware/backplane/bin/backplane.version',
            'firmware/ble/bin/ble.version',
            'firmware/firmware.version',
            'firmware/versions.txt',
            'firmware/blackfin/content/presets/custom/default.json',
            'firmware/blackfin/bin/bootloader.ldr',
            'firmware/ble/bin/nordic_bootloader_0.bin',
            'firmware/blackfin/content/BoOtLoAdEr/future.bin',
            '__MACOSX/x',
            'firmware/.DS_Store',
        ]
        for name in paths:
            with self.subTest(name=name):
                status, role, destination = self.classify(name)
                self.assertEqual(status, 0)
                self.assertEqual((role, destination), destination_for(name))
                self.assertNotEqual(destination, 'os.ldr')

    def test_unknown_paths_and_aliases_never_get_a_destination(self):
        for name in (
            '/os.ldr',
            'os.ldr',
            'firmware/new-mcu/bin/foo.bin',
            'firmware/blackfin/content/../os.ldr',
            'a//b',
            'a/./b',
            'a/b.',
            'firmware/blackfin/content/evil\x00.ldr',
            'a\\b',
            'C:foo',
            'é',
        ):
            with self.subTest(name=name):
                status, _, destination = self.classify(name)
                self.assertNotEqual(status, 0)
                self.assertIsNone(destination)

    def test_stock_version_formats_and_malformed_numbers(self):
        for raw in (b'1.2.5+0', b'1.2.5+0\n', b'1.2.5+0@' + b'a' * 40 + b'\r\n'):
            self.assertEqual(self.native.version(raw), (0, (1, 2, 5, 0)))
        for raw in (b'', b'1.2.5', b'1.2.5+65536', b'1.2.5+0garbage', b'1.2.5+0@bad', b'1.2.5+-1'):
            status, fields = self.native.version(raw)
            self.assertNotEqual(status, 0)
            self.assertIsNone(fields)
