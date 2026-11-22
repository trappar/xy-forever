import hashlib
import random
import struct
import unittest

import pytest

from xy_forever.build_formats import Firmware
from xy_forever.ldr import inspect_ldr, parse_ldr, replay
from xy_forever.outer import ALLOWED, PROTECTED
from xy_forever.paths import DECRYPTED, STOCK_121
from xy_forever.validation import validate_package

# The official OS images (private): the stock 1.2.1 pair's os.ldr and, when decrypted, 1.2.5's; each with its package.
OFFICIAL = (
    ('1.2.1', STOCK_121 / 'os.ldr', DECRYPTED / 'opxy_firmware_1_2_1/firmware_secure.zip'),
    (
        '1.2.5',
        DECRYPTED / 'opxy_firmware_1_2_5/firmware/blackfin/bin/os.ldr',
        DECRYPTED / 'opxy_firmware_1_2_5/opxy_firmware_1_2_5.zip',
    ),
)


def header(flags, target, count, argument=0):
    raw = bytearray(struct.pack('<4I', 0xAD000000 | flags, target, count, argument))
    checksum = 0
    for byte in raw:
        checksum ^= byte
    raw[2] = checksum
    return bytes(raw)


def image(records, entry=0x1000):
    body = b''.join(header(flags, address, count, arg) + payload for flags, address, count, arg, payload in records)
    body += header(0x8004, entry, 0)
    return header(0x5004, entry, 0, len(body)) + body


class Native:
    """native/ldr.hpp through the host extension (hosttest/bind/ldr.cpp)."""

    def __init__(self):
        import xyf_hosttest  # here, not at the top: web tests import this file's helpers without hosttest

        self.native = xyf_hosttest.module('ldr')

    def inspect(self, data, allowed=((0x1000, 0x10000),), protected=()):
        return self.native.inspect(bytes(data), list(allowed), list(protected))

    def expand(self, data, offset, delta, capacity):
        error, output = self.native.expand(bytes(data), offset, delta, capacity)
        if error:
            raise ValueError(f'native expansion failed: {error}')
        return output


class LdrTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.native = Native()

    def test_overlap_and_non_word_fill(self):
        data = image([(4, 0x1000, 16, 0, bytes(range(16))), (0x104, 0x1005, 7, 0x12345678, b'')])
        memory = {}
        entry = replay(data, [(0x1000, 0x2000)], [], lambda at, chunk: memory.update(enumerate(chunk, at)))
        self.assertEqual(entry, 0x1000)
        self.assertEqual(
            bytes(memory[i] for i in range(0x1000, 0x1010)),
            bytes(range(5)) + bytes.fromhex('78563412785634') + bytes(range(12, 16)),
        )
        r = parse_ldr(data)[2]
        self.assertEqual(self.native.expand(data, r.offset, 1, 5), bytes.fromhex('5634127856'))
        self.assertEqual(self.native.inspect(data)[0], 0)

    def test_all_records_checked_before_write(self):
        data = bytearray(image([(4, 0x1000, 4, 0, b'abcd')]))
        data[-1] ^= 1
        writes = []
        with self.assertRaises(ValueError):
            replay(data, [(0x1000, 0x2000)], [], lambda *args: writes.append(args))
        self.assertEqual(writes, [])
        self.assertNotEqual(self.native.inspect(data)[0], 0)

    def test_protected_source_and_unknown_flags(self):
        data = image([(4, 0x1000, 4, 0, b'abcd')])
        self.assertNotEqual(self.native.inspect(data, protected=((0x1002, 0x1003),))[0], 0)
        with self.assertRaises(ValueError):
            replay(data, [(0x1000, 0x2000)], [(0x1002, 0x1003)], lambda *args: None)
        unknown = image([(0x404, 0x1000, 4, 0, b'abcd')])
        with self.assertRaises(ValueError):
            parse_ldr(unknown)
        self.assertNotEqual(self.native.inspect(unknown)[0], 0)

    def test_final_entry_must_survive_ordered_writes(self):
        data = image([(4, 0x1000, 4, 0, b'abcd'), (0x104, 0x1000, 4, 0, b'')])
        with self.assertRaises(ValueError):
            parse_ldr(data)
        self.assertNotEqual(self.native.inspect(data)[0], 0)
        one_byte = image([(4, 0x1000, 1, 0, b'a')])
        with self.assertRaises(ValueError):
            parse_ldr(one_byte)
        self.assertNotEqual(self.native.inspect(one_byte)[0], 0)

    def test_differential_mutations(self):
        rng = random.Random(5549)
        original = image([(4, 0x1000, 20, 0, bytes(range(20))), (0x104, 0x1014, 40, 0x01020304, b'')])
        for _ in range(1800):
            data = bytearray(original)
            for _ in range(rng.randrange(1, 5)):
                data[rng.randrange(len(data))] ^= rng.randrange(1, 256)
            if rng.randrange(4) == 0:
                data = data[: rng.randrange(len(data))]
            try:
                replay(data, [(0x1000, 0x10000)], [], lambda *args: None)
                valid = True
            except ValueError:
                valid = False
            self.assertEqual(self.native.inspect(data)[0] == 0, valid)

    def test_framing_expansion_and_address_limits(self):
        good = image([(4, 0x1000, 4, 0, b'abcd')])
        variants = [
            good + b'x',
            good[:-16],
            image([(4, 0xFFFFFFFE, 4, 0, b'abcd')]),
            image([(4, 0x1000, 4, 0, b'abcd'), (0x104, 0x2000, 0x10000001, 0, b'')]),
            image([(4, 0x1000, 4, 0, b'abcd'), (0x5004, 0x1000, 0, 0, b'')]),
        ]
        for data in variants:
            with self.subTest(size=len(data)):
                with self.assertRaises(ValueError):
                    parse_ldr(data)
                self.assertNotEqual(self.native.inspect(data)[0], 0)

    def test_empty_or_invalid_ram_policies(self):
        data = image([(4, 0x1000, 4, 0, b'abcd')])
        self.assertNotEqual(self.native.inspect(data, allowed=())[0], 0)
        self.assertNotEqual(self.native.inspect(data, allowed=((0x2000, 0x1000),))[0], 0)
        self.assertNotEqual(self.native.inspect(data, protected=((0x1000, 0x1000),))[0], 0)


@pytest.mark.gate  # two whole official images expanded natively and their packages read: about 1 s of CPU
class OfficialImages(unittest.TestCase):
    """The native LDR parser against the independent mapper (build_formats.Firmware) on the official OS images: the
    same records, expanded bytes and entry, and the same final memory; the image matches its package's OS."""

    def test_native_expansion_matches_the_independent_mapper(self):
        native = Native()
        for version, path, package in OFFICIAL:
            with self.subTest(version):
                if not path.is_file():
                    self.skipTest('%s absent (setup.py import-stock; decrypted packages)' % path)
                data = path.read_bytes()
                report = inspect_ldr(data)
                error, summary = native.inspect(data, ALLOWED, PROTECTED)
                self.assertEqual(error, 0)
                self.assertEqual(summary, (report['records'], report['expanded_write_bytes'], int(report['entry'], 16)))
                independent = Firmware(data)
                memory = [(lo, hi, bytearray(hi - lo)) for lo, hi, _ in independent.segments]
                for record in parse_ldr(data)[1:-1]:
                    for delta in range(0, record.count, 65536):
                        chunk = native.expand(data, record.offset, delta, min(65536, record.count - delta))
                        at, end = record.target + delta, record.target + delta + len(chunk)
                        for lo, hi, destination in memory:
                            left, right = max(lo, at), min(hi, end)
                            if left < right:
                                destination[left - lo : right - lo] = chunk[left - at : right - at]
                golden = independent.memory_manifest()
                self.assertEqual(len(memory), len(golden))
                for (lo, hi, destination), row in zip(memory, golden):
                    self.assertEqual(hashlib.sha256(destination).hexdigest(), row['sha256'], '%#x..%#x' % (lo, hi))
                if package.is_file():
                    self.assertEqual(validate_package(package)[1]['os']['sha256'], report['sha256'])


if __name__ == '__main__':
    unittest.main()
