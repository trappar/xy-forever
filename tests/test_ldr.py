import ctypes
import random
from pathlib import Path
import struct
import tempfile
import unittest

from xy_forever.ldr import parse_ldr, replay

from xy_forever import hostbuild

ROOT = Path(__file__).resolve().parents[1]

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

class Range(ctypes.Structure):
    _fields_ = [('begin', ctypes.c_uint), ('end', ctypes.c_uint)]

class Summary(ctypes.Structure):
    _fields_ = [('records', ctypes.c_uint), ('expanded', ctypes.c_uint), ('entry', ctypes.c_uint)]

class Native:
    def __init__(self, directory):
        self.path = Path(directory) / 'ldr.dylib'
        hostbuild.run(['/usr/bin/clang++', '-std=c++11', '-Wall', '-Wextra', '-Werror',
                       '-shared', '-fPIC', str(ROOT / 'native/ldr.cpp'),
                       str(ROOT / 'tests/native_bridge.cpp'), '-o', str(self.path)], check=True)
        self.lib = ctypes.CDLL(str(self.path))
        byte_ptr = ctypes.POINTER(ctypes.c_ubyte)
        word = ctypes.c_uint
        self.lib.inspect_ldr.argtypes = [byte_ptr, word, ctypes.POINTER(Range), word,
                                        ctypes.POINTER(Range), word, ctypes.POINTER(Summary)]
        self.lib.expand_ldr.argtypes = [byte_ptr, word, word, word, byte_ptr, word, ctypes.POINTER(word)]
        self.lib.inspect_ldr.restype = self.lib.expand_ldr.restype = ctypes.c_int

    def inspect(self, data, allowed=((0x1000, 0x10000),), protected=()):
        raw = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
        ranges = lambda rs: (Range * len(rs))(*(Range(*r) for r in rs))
        result = Summary()
        error = self.lib.inspect_ldr(raw, len(data), ranges(allowed), len(allowed),
                                     ranges(protected), len(protected), ctypes.byref(result))
        return error, result

    def expand(self, data, offset, delta, capacity):
        raw = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
        output = (ctypes.c_ubyte * capacity)()
        produced = ctypes.c_uint()
        error = self.lib.expand_ldr(raw, len(data), offset, delta, output, capacity, ctypes.byref(produced))
        if error:
            raise ValueError(f'native expansion failed: {error}')
        return bytes(output[:produced.value])

class LdrTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='xyf-tests-')
        cls.native = Native(cls.tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

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
