"""Differential and malformed-input tests for the freestanding ZIP32 decoder."""
from concurrent.futures import ThreadPoolExecutor
import io
from pathlib import Path
import random
import stat
import struct
import subprocess
import tempfile
import unittest
import warnings
import zipfile
import zlib

import pytest


from xy_forever import hostbuild

ROOT = Path(__file__).resolve().parents[1]


class Bits:
    def __init__(self):
        self.bits = []

    def integer(self, value, width):
        self.bits.extend((value >> i) & 1 for i in range(width))

    def code(self, value, width):
        self.bits.extend((value >> i) & 1 for i in range(width - 1, -1, -1))

    def fixed(self, symbol):
        if symbol < 144:
            self.code(symbol + 0x30, 8)
        elif symbol < 256:
            self.code(symbol - 144 + 0x190, 9)
        elif symbol < 280:
            self.code(symbol - 256, 7)
        else:
            self.code(symbol - 280 + 0xC0, 8)

    def data(self):
        result = bytearray((len(self.bits) + 7) // 8)
        for i, bit in enumerate(self.bits):
            result[i // 8] |= bit << (i % 8)
        return bytes(result)


def raw(data, *, level=6, strategy=zlib.Z_DEFAULT_STRATEGY):
    compressor = zlib.compressobj(level, zlib.DEFLATED, -15, strategy=strategy)
    return compressor.compress(data) + compressor.flush()


def zip_bytes(rows, compression=zipfile.ZIP_DEFLATED):
    stream = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(stream, "w", compression=compression) as archive:
            for name, data in rows:
                archive.writestr(name, data)
    return stream.getvalue()


def manual_zip(name, data, compressed, *, method=8, flags=0, descriptor=None):
    name = name.encode()
    crc = zlib.crc32(data)
    sizes = (0, 0, 0) if descriptor else (crc, len(compressed), len(data))
    local = struct.pack("<I5H3I2H", 0x04034B50, 20, flags, method, 0, 0, *sizes, len(name), 0) + name + compressed
    if descriptor:
        if descriptor == "signed":
            local += struct.pack("<I", 0x08074B50)
        local += struct.pack("<3I", crc, len(compressed), len(data))
    central = struct.pack("<I6H3I5H2I", 0x02014B50, 20, 20, flags, method, 0, 0, crc,
                          len(compressed), len(data), len(name), 0, 0, 0, 0, 0, 0) + name
    return local + central + struct.pack("<I4H2IH", 0x06054B50, 0, 0, 1, 1, len(central), len(local), 0)


@pytest.mark.gate
class NativeZipTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="xyf-synthetic-")
        cls.directory = Path(cls.temporary.name)
        cls.bridge = cls.directory / "zip_bridge"
        cls.command = ["/usr/bin/clang++", "-std=c++11", "-O2", "-Wall", "-Wextra", "-Werror",
                       str(ROOT / "tests/zip_bridge.cpp"), str(ROOT / "native/zip.cpp"),
                       str(ROOT / "native/inflate.cpp"), "-o", str(cls.bridge)]
        hostbuild.run(cls.command, check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def run_bridge(self, command, data, *args, binary=None, name="input"):
        path = self.directory / (name + ".bin")                 # name: runs at the same time
        path.write_bytes(data)
        return subprocess.run([str(binary or self.bridge), command, str(path), *map(str, args)], capture_output=True)

    def expect_raw(self, compressed, expected, *, valid=True):
        result = self.run_bridge("raw", compressed, len(expected))
        if valid:
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, expected)
        else:
            self.assertNotEqual(result.returncode, 0)
        return result

    def test_stored_fixed_dynamic_and_multiblock_against_zlib(self):
        rng = random.Random(5123)
        inputs = [b"", b"hello", b"a" * 100000, bytes(range(256)) * 600,
                  bytes(rng.randrange(256) for _ in range(70000)),
                  (b"one two three four five \x00\x80\xff" * 10000)]
        for data in inputs:
            for level, strategy in [(0, zlib.Z_DEFAULT_STRATEGY), (6, zlib.Z_FIXED),
                                    (6, zlib.Z_DEFAULT_STRATEGY), (9, zlib.Z_HUFFMAN_ONLY)]:
                with self.subTest(size=len(data), level=level, strategy=strategy):
                    self.expect_raw(raw(data, level=level, strategy=strategy), data)

    def test_maximum_history_distance_and_overlapping_matches(self):
        seed = bytes((i * 37 + i // 256) % 256 for i in range(32768))
        stored = b"\x00" + struct.pack("<HH", len(seed), 65535 ^ len(seed)) + seed
        bits = Bits()
        bits.integer(1, 1)
        bits.integer(1, 2)
        bits.fixed(285)  # length 258
        bits.code(29, 5)
        bits.integer(8191, 13)  # distance 32768
        bits.fixed(256)
        compressed = stored + bits.data()
        expected = seed + seed[:258]
        self.assertEqual(zlib.decompress(compressed, -15), expected)
        self.expect_raw(compressed, expected)
        self.expect_raw(raw(b"x" * 500000), b"x" * 500000)

    def test_single_end_code_and_empty_distance_tree(self):
        bits = Bits()
        bits.integer(1, 1)
        bits.integer(2, 2)
        bits.integer(0, 5)
        bits.integer(0, 5)
        bits.integer(14, 4)  # 18 code-length alphabet entries through symbol1
        order = [16,17,18,0,8,7,9,6,10,5,11,4,12,3,13,2,14,1]
        for code in order:
            bits.integer({18: 1, 0: 2, 1: 2}.get(code, 0), 3)
        for count in (138, 118):
            bits.code(0, 1)
            bits.integer(count - 11, 7)
        bits.code(3, 2)  # literal 256 has one-bit code
        bits.code(2, 2)  # empty distance alphabet
        bits.code(0, 1)  # end-of-block
        compressed = bits.data()
        self.assertEqual(zlib.decompress(compressed, -15), b"")
        self.expect_raw(compressed, b"")

    def test_reserved_symbols_invalid_distance_and_bad_stored_length(self):
        malformed = [b"\x07", b"\x01\x01\x00\x00\x00x"]
        for kind in ("literal", "distance", "before_history"):
            bits = Bits()
            bits.integer(1, 1)
            bits.integer(1, 2)
            if kind == "literal":
                bits.fixed(286)
            else:
                if kind == "distance":
                    bits.fixed(65)
                bits.fixed(257)
                bits.code(30 if kind == "distance" else 0, 5)
            malformed.append(bits.data())
        for data in malformed:
            with self.subTest(data=data):
                self.expect_raw(data, b"xxxx", valid=False)

    def test_invalid_dynamic_trees_and_repeat_overflow(self):
        streams = []
        for lengths in ([1, 1, 1, 1], [0, 0, 0, 0], [1, 0, 0, 1], [0, 0, 1, 1]):
            bits = Bits()
            bits.integer(1, 1)
            bits.integer(2, 2)
            bits.integer(0, 5)
            bits.integer(0, 5)
            bits.integer(0, 4)
            for length in lengths:
                bits.integer(length, 3)
            if lengths == [1, 0, 0, 1]:
                bits.code(1, 1)  # repeat previous before a previous length exists
                bits.integer(0, 2)
            if lengths == [0, 0, 1, 1]:
                for _ in range(2):
                    bits.code(1, 1)  # repeat138 twice exceeds258 total lengths
                    bits.integer(127, 7)
            streams.append(bits.data())
        bits = Bits()
        bits.integer(1, 1)
        bits.integer(2, 2)
        bits.integer(31, 5)  # HLIT288 is forbidden
        bits.integer(0, 5)
        bits.integer(0, 4)
        streams.append(bits.data())
        for data in streams:
            with self.subTest(stream=data):
                self.expect_raw(data, b"", valid=False)

    def test_raw_truncation_output_bounds_and_trailing_bytes(self):
        data = b"alphabet soup " * 500
        compressed = raw(data)
        for size in range(len(compressed)):
            with self.subTest(cut=size):
                self.expect_raw(compressed[:size], data, valid=False)
        self.expect_raw(compressed + b"\x00", data, valid=False)
        for expected in (b"", data[:-1], data + b"x"):
            self.expect_raw(compressed, expected, valid=False)

    def test_stored_and_deflated_zip_extract(self):
        for method in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            data = zip_bytes([("firmware/blackfin/bin/os.ldr", b"os" * 40000),
                              ("content/display/test.svg", b"svg"), ("content/empty", b"")], method)
            result = self.run_bridge("extract", data, "firmware/blackfin/bin/os.ldr")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, b"os" * 40000)
            self.assertEqual(self.run_bridge("extract", data, "content/empty").returncode, 0)

    def test_signed_and_unsigned_data_descriptors(self):
        data = b"descriptor" * 500
        for descriptor in ("signed", "unsigned"):
            archive = manual_zip("os.ldr", data, raw(data), flags=8, descriptor=descriptor)
            self.assertEqual(zipfile.ZipFile(io.BytesIO(archive)).read("os.ldr"), data)
            result = self.run_bridge("extract", archive, "os.ldr")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, data)

    def test_local_central_disagreement_overlap_and_gaps_rejected(self):
        good = zip_bytes([("first", b"first"), ("second", b"second")])
        central = good.index(b"PK\x01\x02")
        variants = []
        for local_offset in (6, 8, 14, 18, 22, 26, 30):
            changed = bytearray(good)
            changed[local_offset] ^= 1
            variants.append(changed)
        changed = bytearray(good)
        second_central = good.index(b"PK\x01\x02", central + 4)
        struct.pack_into("<I", changed, second_central + 42, 0)
        variants.append(changed)
        variants.append(b"prefix" + good)
        for data in variants:
            with self.subTest(data=bytes(data[:32])):
                self.assertNotEqual(self.run_bridge("list", data).returncode, 0)

    def test_crc_failure_and_real_output_length_enforced(self):
        data = b"data" * 10000
        good = manual_zip("os.ldr", data, raw(data))
        central = good.index(b"PK\x01\x02")
        bad_crc = bytearray(good)
        bad_crc[14] ^= 1
        bad_crc[central + 16] ^= 1
        result = self.run_bridge("extract", bad_crc, "os.ldr")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stderr.strip(), b"checksum")
        for size in (len(data) - 1, len(data) + 1):
            changed = bytearray(good)
            struct.pack_into("<I", changed, 22, size)
            struct.pack_into("<I", changed, central + 24, size)
            self.assertNotEqual(self.run_bridge("extract", changed, "os.ldr").returncode, 0)

    def test_unsafe_names_aliases_and_special_files_rejected(self):
        for name in ("../os.ldr", "/os.ldr", "a//b", "a/../b", "a\\b", "C:/x", "a/.", "a.", "a ", "\u00e9", "a\x01b"):
            with self.subTest(name=name):
                self.assertNotEqual(self.run_bridge("list", zip_bytes([(name, b"x")])).returncode, 0)
        for rows in ([('a', b'1'), ('A', b'2')], [('a', b'1'), ('a/b', b'2')], [('a', b'1'), ('a/', b'')]):
            self.assertNotEqual(self.run_bridge("list", zip_bytes(rows)).returncode, 0)
        link = zipfile.ZipInfo("link")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        self.assertNotEqual(self.run_bridge("list", zip_bytes([(link, b"elsewhere")])).returncode, 0)

    def test_directory_entries_and_archive_comments(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("content/", b"")
            archive.writestr("content/a", b"a")
            archive.comment = b"comment with PK\x05\x06 signature"
        result = self.run_bridge("extract", stream.getvalue(), "content/a")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, b"a")

    def test_maximum_name_and_extra_field_boundaries(self):
        for size, expected in ((255, 0), (256, 1)):
            result = self.run_bridge("list", zip_bytes([("a" * size, b"value")]))
            self.assertEqual(result.returncode, expected, result.stderr)
        for extra, expected in ((struct.pack("<HH", 1, 0), 1),
                                (struct.pack("<HH", 0x7075, 0), 1),
                                (struct.pack("<HH", 0x5455, 1), 1),
                                (struct.pack("<HHB", 0x5455, 1, 0), 0)):
            info = zipfile.ZipInfo("os.ldr")
            info.extra = extra
            result = self.run_bridge("extract", zip_bytes([(info, b"value")]), "os.ldr")
            self.assertEqual(result.returncode, expected, result.stderr)

    def test_central_order_independent_and_corrupt_descriptor_rejected(self):
        data = zip_bytes([("first", b"one"), ("second", b"two")])
        central = data.index(b"PK\x01\x02")
        second = data.index(b"PK\x01\x02", central + 4)
        end = data.index(b"PK\x05\x06", second + 4)
        reordered = data[:central] + data[second:end] + data[central:second] + data[end:]
        result = self.run_bridge("extract", reordered, "first")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, b"one")
        descriptor = bytearray(manual_zip("os.ldr", b"hello", raw(b"hello"), flags=8, descriptor="signed"))
        marker = descriptor.index(b"PK\x07\x08")
        descriptor[marker + 4] ^= 1
        self.assertNotEqual(self.run_bridge("list", descriptor).returncode, 0)

    def test_limits_capacity_ratio_and_unsupported_formats(self):
        good = zip_bytes([("a", b"x" * 100), ("b", b"b")])
        for args in ((0,), (1,), (4096, 99), (4096, 100, 100), (4096, 100, 101, 1)):
            with self.subTest(args=args):
                self.assertNotEqual(self.run_bridge("list", good, "unused", *args).returncode, 0)
        central = good.index(b"PK\x01\x02")
        variants = []
        for field, value, width in ((8, 1, "H"), (10, 99, "H"), (20, 0xFFFFFFFF, "I"), (34, 1, "H")):
            changed = bytearray(good)
            struct.pack_into("<" + width, changed, central + field, value)
            variants.append(changed)
        changed = bytearray(good)
        struct.pack_into("<H", changed, len(changed) - 18, 1)
        variants.append(changed)
        for archive in variants:
            self.assertNotEqual(self.run_bridge("list", archive).returncode, 0)

    def test_read_and_sink_failures_propagate(self):
        archive = zip_bytes([("a", b"read error")])
        for command, status in (("fail-read", b"io"), ("fail-sink", b"sink")):
            result = self.run_bridge(command, archive, "a")
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stderr.strip(), status)

    def test_freestanding_objects_have_no_runtime_symbols(self):
        objects = []
        for source in ("zip", "inflate"):
            target = self.directory / (source + ".o")
            command = ["/usr/bin/clang++", "-std=c++11", "-O2", "-ffreestanding", "-fno-builtin",
                       "-fno-exceptions", "-fno-rtti", "-fno-stack-protector", "-c",
                       str(ROOT / ("native/" + source + ".cpp")), "-o", str(target)]
            hostbuild.run(command, check=True, capture_output=True)
            objects.append(target)
        output = subprocess.run(["/usr/bin/nm", "-u", *map(str, objects)], check=True, capture_output=True, text=True).stdout
        for line in output.splitlines():
            if " U " in line:
                self.assertIn("xyf_core3zip", line)

    def test_deterministic_damage_under_address_and_undefined_sanitizers(self):
        sanitized = self.directory / "zip_bridge_sanitized"
        command = list(self.command)
        command[command.index("-O2")] = "-O1"
        command[-1] = str(sanitized)
        command[1:1] = ["-fsanitize=address,undefined", "-fno-omit-frame-pointer"]
        hostbuild.run(command, check=True, capture_output=True)
        rng = random.Random(1951)
        original = zip_bytes([("os.ldr", b"abc" * 3000)])
        compressed = raw(b"abc" * 3000)
        runs = []
        for iteration in range(100):
            if iteration & 1:
                data = bytearray(original)
                command, args = "extract", ("os.ldr",)
            else:
                data = bytearray(compressed)
                command, args = "raw", (9000,)
            if iteration % 3 == 0:
                del data[rng.randrange(len(data)):]
            else:
                data[rng.randrange(len(data))] ^= rng.randrange(1, 256)
            runs.append((command, data, args))
        with ThreadPoolExecutor(max_workers=6) as pool:              # independent runs, each its own input
            results = list(pool.map(lambda n: self.run_bridge(runs[n][0], runs[n][1], *runs[n][2], binary=sanitized,
                                                              name="damaged-%d" % n), range(len(runs))))
        for result in results:
            self.assertNotIn(b"AddressSanitizer", result.stderr)
            self.assertNotIn(b"runtime error:", result.stderr)
            self.assertIn(result.returncode, (0, 1))


if __name__ == "__main__":
    unittest.main()
