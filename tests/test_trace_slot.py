import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from xy_forever import hostbuild  # noqa: E402


def header(flags, address, count, argument=0):
    data = bytearray(struct.pack('<4I', 0xad000000 | flags, address, count, argument))
    checksum = 0
    for byte in data:
        checksum ^= byte
    data[2] = checksum
    return bytes(data)


def image(records=(), entry=0x1000):
    body = header(4, 0x1000, 4) + b'boot'
    for flags, address, count, argument, data in records:
        body += header(flags, address, count, argument) + data
    body += header(0x8004, entry, 0)
    return header(0x5004, entry, 0, len(body)) + body


def data_record(address, data):
    return (4, address, len(data), 0, data)


@pytest.mark.gate
class TraceSlotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='trace-slot-')
        cls.directory = Path(cls.temp.name)
        cls.binary = cls.directory / 'bridge'
        flags = ['-fsanitize=address,undefined', '-fno-omit-frame-pointer'] if os.getenv('TRACE_SLOT_SANITIZE') else []
        hostbuild.run(['/usr/bin/clang++', '-std=c++11', '-Wall', '-Wextra', '-Werror',
                       *flags, str(ROOT / 'native/trace_slot.cpp'), str(ROOT / 'native/ldr.cpp'),
                       str(ROOT / 'tests/trace_slot_bridge.cpp'), '-o', str(cls.binary)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def edit(self, source, expected=b'old!', replacement=b'new!', target=0x2000,
             count=None, mode='normal', status='ok'):
        paths = [self.directory / name for name in ('input', 'expected', 'replacement', 'output')]
        for path, content in zip(paths, (source, expected, replacement)):
            path.write_bytes(content)
        command = [str(self.binary), *map(str, paths), hex(target),
                   str(len(expected) if count is None else count), mode]
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        actual, offset, validation = result.stdout.strip().split()
        self.assertEqual(actual, status, result.stderr)
        output = paths[3].read_bytes()
        if status != 'ok':
            self.assertEqual(output, source)
            self.assertEqual(int(offset), 0xffffffff)
        return output, int(offset), validation

    def test_only_exact_slot_bytes_change_and_headers_remain_identical(self):
        source = image([data_record(0x1800, b'before'), data_record(0x2000, b'old!'),
                        data_record(0x3000, b'after')])
        output, offset, validation = self.edit(source)
        self.assertEqual(output, source[:offset] + b'new!' + source[offset + 4:])
        self.assertEqual(output[offset - 16:offset], header(4, 0x2000, 4))
        self.assertEqual(validation, 'ok')

    def test_minimum_maximum_and_unaligned_slots(self):
        for size in (1, 3, 360, 4096):
            with self.subTest(size=size):
                expected = bytes(i % 251 for i in range(size))
                replacement = bytes(255 - i % 251 for i in range(size))
                source = image([data_record(0x2001, expected)])
                output, offset, _ = self.edit(source, expected, replacement, target=0x2001)
                self.assertEqual(output, source[:offset] + replacement + source[offset + size:])

    def test_whole_preimage_checked_before_first_write(self):
        source = image([data_record(0x2000, b'old!')])
        for expected in (b'bad!', b'old?'):
            self.edit(source, expected=expected, status='preimage_mismatch')

    def test_malformed_final_record_never_writes_slot(self):
        source = bytearray(image([data_record(0x2000, b'old!')]))
        source[-1] ^= 1
        _, _, validation = self.edit(bytes(source), status='invalid_image')
        self.assertEqual(validation, 'header')

    def test_truncated_streams_and_invalid_first_extent(self):
        source = image([data_record(0x2000, b'old!')])
        for size in (0, 15, 31, 36, len(source) - 1):
            with self.subTest(size=size):
                self.edit(source[:size], status='invalid_image')
        self.edit(header(0x5004, 0x1000, 0, len(source)) + source[16:], status='invalid_image')

    def test_supplied_policy_rejection_precedes_mutation(self):
        source = image([data_record(0x2000, b'old!')])
        _, _, validation = self.edit(source, mode='protect', status='invalid_image')
        self.assertEqual(validation, 'protected_range')
        self.edit(image([data_record(0x10000, b'old!')]), target=0x10000, status='invalid_image')

    def test_missing_exact_slot_and_fill_only(self):
        for records in ([], [data_record(0x2000, b'old!!')],
                        [(0x104, 0x2000, 4, 0x21646c6f, b'')]):
            with self.subTest(records=records):
                self.edit(image(records), status='missing_slot')

    def test_duplicate_exact_slot_refused_even_when_bytes_match(self):
        source = image([data_record(0x2000, b'old!'), data_record(0x2000, b'old!')])
        self.edit(source, status='duplicate_slot')

    def test_other_data_and_fill_overlays_before_and_after_slot(self):
        exact = data_record(0x2000, b'old!')
        for flags in (4, 0x104):
            for address, size in ((0x1fff, 2), (0x2003, 2), (0x1fff, 8), (0x2001, 1)):
                overlay = (flags, address, size, 0, b'x' * size if flags == 4 else b'')
                for records in ([exact, overlay], [overlay, exact]):
                    with self.subTest(flags=flags, address=address, size=size, records=records):
                        self.edit(image(records), status='overlapping_record')

    def test_adjacent_records_and_zero_length_records_are_allowed(self):
        source = image([data_record(0x1fff, b'x'), data_record(0x2000, b'old!'),
                        (0x104, 0x2004, 4, 0, b''), data_record(0x2001, b'')])
        self.edit(source)

    def test_entry_first_or_second_byte_overlap_refused(self):
        self.edit(image(), expected=b'boot', replacement=b'xxxx', target=0x1000,
                  status='entry_overlap')
        # The final entry halfword straddles two DATA records; the selected
        # one-byte slot owns only its second byte.
        source = image([data_record(0x3000, b'a'), data_record(0x3001, b'b')], entry=0x3000)
        self.edit(source, expected=b'b', replacement=b'c', target=0x3001, status='entry_overlap')

    def test_replacement_aliases_both_ends_and_prefix_are_refused(self):
        source = image([data_record(0x2000, b'old!')])
        for mode in ('alias_start', 'alias_end', 'alias_prefix'):
            with self.subTest(mode=mode):
                self.edit(source, mode=mode, status='replacement_alias')
        self.edit(source, mode='alias_after')

    def test_null_pointers_and_count_limits_are_refused(self):
        source = image([data_record(0x2000, b'old!')])
        for mode in ('null_image', 'null_expected', 'null_replacement'):
            with self.subTest(mode=mode):
                self.edit(source, mode=mode, status='invalid_argument')
        self.edit(source, count=0, status='invalid_argument')
        self.edit(source, expected=b'x' * 4097, replacement=b'y' * 4097, status='invalid_argument')
        self.edit(source, target=0xfffffffe, status='invalid_argument')

    def test_stale_preimage_retry_is_refused(self):
        source = image([data_record(0x2000, b'old!')])
        output, _, _ = self.edit(source)
        self.edit(output, status='preimage_mismatch')


if __name__ == '__main__':
    unittest.main()
