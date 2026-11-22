import random
import struct
import unittest

from xy_forever.ldr import parse_ldr, replay

import xyf_hosttest

def header(flags, target, count, argument=0):
    raw = bytearray(struct.pack('<4I', 0xad000000 | flags, target, count, argument))
    checksum = 0
    for byte in raw:
        checksum ^= byte
    raw[2] = checksum
    return bytes(raw)

def image(records, entry=0x1000):
    body = b''.join(header(flags, address, count, arg) + payload
                    for flags, address, count, arg, payload in records)
    body += header(0x8004, entry, 0)
    return header(0x5004, entry, 0, len(body)) + body

class Native:
    """native/ldr.hpp through the host extension (hosttest/bind/ldr.cpp)."""
    def __init__(self):
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
        data = image([(4, 0x1000, 16, 0, bytes(range(16))),
                      (0x104, 0x1005, 7, 0x12345678, b'')])
        memory = {}
        entry = replay(data, [(0x1000, 0x2000)], [],
                       lambda at, chunk: memory.update(enumerate(chunk, at)))
        self.assertEqual(entry, 0x1000)
        self.assertEqual(bytes(memory[i] for i in range(0x1000, 0x1010)),
                         bytes(range(5)) + bytes.fromhex('78563412785634') + bytes(range(12, 16)))
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
        original = image([(4, 0x1000, 20, 0, bytes(range(20))),
                          (0x104, 0x1014, 40, 0x01020304, b'')])
        for _ in range(1800):
            data = bytearray(original)
            for _ in range(rng.randrange(1, 5)):
                data[rng.randrange(len(data))] ^= rng.randrange(1, 256)
            if rng.randrange(4) == 0:
                data = data[:rng.randrange(len(data))]
            try:
                replay(data, [(0x1000, 0x10000)], [], lambda *args: None)
                valid = True
            except ValueError:
                valid = False
            self.assertEqual(self.native.inspect(data)[0] == 0, valid)

    def test_framing_expansion_and_address_limits(self):
        good = image([(4, 0x1000, 4, 0, b'abcd')])
        variants = [good + b'x', good[:-16],
                    image([(4, 0xfffffffe, 4, 0, b'abcd')]),
                    image([(4, 0x1000, 4, 0, b'abcd'), (0x104, 0x2000, 0x10000001, 0, b'')]),
                    image([(4, 0x1000, 4, 0, b'abcd'), (0x5004, 0x1000, 0, 0, b'')])]
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

if __name__ == '__main__':
    unittest.main()
