"""Report integrity and exact inner-OS mutation boundaries; no device execution."""

import os
import struct
import subprocess
import tempfile
import unittest
import zlib
from pathlib import Path

import xyf_hosttest

from xy_forever.build_formats import header
from xy_forever.probe_report import decode, report_only_fixture
from xy_forever.xyf_build import (
    CARRY,
    END,
    HEAP_BASE,
    HEAP_BYTES,
    REPORTER,
    heap_record,
    inner_image,
    patches,
    verify_inner,
)

ROOT = Path(__file__).resolve().parents[1]


def synthetic_stock():
    records = [header(0x104, HEAP_BASE, HEAP_BYTES, 0)]
    for address, before, _ in patches():
        records.append(header(4, address, len(before), 0) + before)
    records.extend([header(4, 0x11A00000, 4, 0) + b'\0\0\0\0', header(0x8004, 0x11A00000, 0, 0)])
    body = b''.join(records)
    return header(0x5004, 0x11A00000, 0, len(body)) + body


class StorageProbeTests(unittest.TestCase):
    def test_cold_snapshot_readiness_is_not_a_storage_success(self):
        words = [0] * 90
        words[:3] = [0x32525058, 1, 360]
        words[4:8] = [5, 1, 1, 1]  # snapshot captured, IRQ readiness refused
        words[11:15] = [0x53444358, 1, 1, 5]
        words[17 + 4] = 3

        def packed():
            words[3] = 0
            words[3] = zlib.crc32(struct.pack('<90I', *words))
            return struct.pack('<90I', *words)

        result = decode(packed())
        self.assertTrue(result['cold_snapshot_captured'])
        self.assertEqual(result['begin'], 'irq')
        self.assertFalse(result['probe_passed'])
        self.assertFalse(result['writer_fixture_matched'])
        self.assertEqual(result['before']['references'], 3)
        words[79] = 4
        words[13] = 6
        result = decode(packed())
        self.assertFalse(result['cold_snapshot_captured'])
        self.assertEqual(result['heap_result'], 'staging_overlap')
        self.assertFalse(result['probe_passed'])

    def test_lookup_trace_version3_decodes_and_rejects_trailing_data(self):
        words = [0] * 90
        words[:3] = [0x32525058, 1, 360]
        words[4:8] = [2, 1, 1, 1]  # identity refusal from failed lookup
        words[11:15] = [0x53444358, 3, 6, 2]
        words[48:56] = [10, 0, 0, 2, 0x11907F90, 0x11907F8C, 0x11907F8C, 0]

        def packed():
            words[3] = 0
            words[3] = zlib.crc32(struct.pack('<90I', *words))
            return struct.pack('<90I', *words)

        result = decode(packed())
        self.assertNotIn('after', result)
        self.assertEqual(result['lookup']['result'], 'missing')
        self.assertEqual(result['lookup']['sentinel_next'], '0x11907f8c')
        self.assertEqual(result['lookup']['reads'], 2)
        self.assertFalse(result['cold_snapshot_captured'])
        self.assertFalse(result['probe_passed'])
        words[60] = 1
        with self.assertRaisesRegex(ValueError, 'trailing'):
            decode(packed())

    def test_construct_trace_version4_decodes(self):
        words = [0] * 90
        words[:3] = [0x32525058, 1, 360]
        words[4:8] = [0, 1, 1, 1]
        words[11:15] = [0x53444358, 4, 1, 0]
        device = 0x88AA5598
        words[17 + 3], words[17 + 4], words[17 + 28] = 1, 1, device
        words[48:58] = [0, 1, 8, 12, 0, device + 0xB4, device + 0xB4, device, 0, 0x88AA5588]
        words[79], words[84] = 0, 0x88AA6438

        def packed():
            words[3] = 0
            words[3] = zlib.crc32(struct.pack('<90I', *words))
            return struct.pack('<90I', *words)

        construct = decode(packed())['construct']
        self.assertTrue(construct['registered'])
        self.assertEqual(construct['shape_mismatch'], [])
        self.assertEqual(construct['heap_brk_after'], '0x88aa6438')
        words[56] = (1 << 13) | (1 << 26)
        construct = decode(packed())['construct']
        self.assertFalse(construct['registered'])
        self.assertEqual(construct['shape_mismatch'], ['write_chunk', 'mounted'])
        words[56] = 0xFFFFFFFF
        self.assertEqual(decode(packed())['construct']['shape_mismatch'], 'not checked')
        words[56], words[70] = 0, 1
        with self.assertRaisesRegex(ValueError, 'trailing'):
            decode(packed())

    def test_construct_span_check_version5(self):
        words = [0] * 90
        words[:3] = [0x32525058, 1, 360]
        words[4:8] = [2, 1, 1, 1]
        words[11:15] = [0x53444358, 5, 1, 2]
        words[48:58] = [10, 0, 0, 2, 0x11907F90, 0x11907F8C, 0x11907F8C, 0, 0xFFFFFFFF, 0x88AA5588]
        words[59 + 3] = 1 << (110 - 96)  # chunk 110: heap block offset 0x5f0
        words[63:67] = [0x11111111, 0x22222222, 0x33333333, 0x00000200]
        words[3] = zlib.crc32(struct.pack('<90I', *words))
        construct = decode(struct.pack('<90I', *words))['construct']
        self.assertEqual(
            construct['span_mismatch'],
            [dict(span='heap_block', address='0x88a2f25c', observed='111111112222222233333333', note=None)],
        )
        self.assertFalse(construct['registered'])

    def test_fresh_mount_version6_decodes_pass_and_failure(self):
        words = [0] * 90
        words[:3] = [0x32525058, 1, 360]
        words[4:11] = [0, 0, 0, 1, 512, 512, 0]
        words[11:17] = [0x53444358, 6, 5, 0, 0, 0]
        words[17 + 28] = 0x88AA5598
        words[48 + 24 : 48 + 28] = [3906, 2048, 2, 0]  # ~10 ms, 2 KiB, 2 images ok, exact shape
        words[84] = 0x88C00000

        def packed():
            words[3] = 0
            words[3] = zlib.crc32(struct.pack('<90I', *words))
            return struct.pack('<90I', *words)

        result = decode(packed())
        fresh = result['fresh_mount']
        self.assertTrue(fresh['passed'])
        self.assertEqual((fresh['images_listed'], fresh['list_result'], fresh['mount_status']), (2, 'ok', 0))
        self.assertEqual(fresh['mount_ms'], 10.0)
        self.assertNotIn('log_callback0', result['after'])
        self.assertFalse(result['probe_passed'])
        words[8] = 512 | (0xFFFF << 16)  # mount returned -1
        words[4], words[13], words[14] = 9, 6, 9
        fresh = decode(packed())['fresh_mount']
        self.assertEqual(fresh['mount_status'], -1)
        self.assertFalse(fresh['passed'])

    def test_fresh_io_version7_decodes(self):
        words = [0] * 90
        words[:3] = [0x32525058, 1, 360]
        words[4:11] = [0, 0, 0, 1, 512, 512, 0]
        words[11:17] = [0x53444358, 7, 5, 0, 0, 0]
        io = [0] * 31
        io[0:8] = [1757813, 6400, 0, 3, 5, 512, 0, 0]
        io[9:15] = [0x88B00000, 2 | (2 << 16), 0, 0x88B00100, 3 | (1 << 16), 0]
        io[16:28] = list(
            struct.unpack('<12I', b'chained-loader\0\0' + b'os.ldr'.ljust(16, b'\0') + b'presets'.ljust(16, b'\0'))
        )
        words[48:79] = io
        words[3] = zlib.crc32(struct.pack('<90I', *words))
        fresh = decode(struct.pack('<90I', *words))['fresh_io']
        self.assertTrue(fresh['passed'])
        self.assertEqual(fresh['root_first_names'], ['chained-loader', 'os.ldr', 'presets'])
        self.assertEqual(fresh['images'], dict(opened=True, entries=2, regular_files=2, closedir=0))
        self.assertEqual(fresh['mount_ms'], 4500.0)

    def test_fresh_io_version8_stock_payload(self):
        from xy_forever.probe_report import STOCK121_OS

        words = [0] * 90
        words[:3] = [0x32525058, 1, 360]
        words[4:11] = [0, 0, 0, 1, 512, 512, 0]
        words[11:17] = [0x53444358, 8, 5, 0, 0, 0]
        io = [0] * 31
        io[0:8] = [24690, 7000, 0, 3, 0, 512, 0, 0]
        io[9:15] = [0x88B00000, 3 | (3 << 16), 0, 0x88B00100, 12 | (5 << 16), 0]
        io[16:24] = list(struct.unpack('<8I', b'firmware.versio\0' + b'layout.json'.ljust(16, b'\0')))
        io[24:28] = [STOCK121_OS[0], STOCK121_OS[1], 390625, 1 | (0 << 16) | (0 << 24)]
        words[48:79] = io
        words[3] = zlib.crc32(struct.pack('<90I', *words))
        fresh = decode(struct.pack('<90I', *words))['fresh_io']
        self.assertTrue(fresh['stock_payload']['matches_stock121'])
        self.assertEqual(fresh['stock_payload']['ms'], 1000.0)
        self.assertEqual(fresh['root_first_names'], ['firmware.versio', 'layout.json'])
        self.assertTrue(fresh['passed'])

    def test_storage_boot_version9_decodes(self):
        words = [0] * 90
        words[:3] = [0x32525058, 1, 360]
        words[4:11] = [0, 0, 0, 1, 512, 512, 0]
        words[11:17] = [0x53444358, 9, 5, 0, 0, 0]
        io = [0] * 31
        io[0:8] = [24690, 7000, 0, 3, 0, 512, 0, 0]
        io[9:15] = [0x88B00000, 4 | (4 << 16), 0, 0x88B00100, 12 | (5 << 16), 0]
        io[24:28] = [2380000, 0x12345678, 195312, 1]
        io[30] = 1 | (1 << 8)
        words[48:79] = io

        def packed():
            words[3] = 0
            words[3] = zlib.crc32(struct.pack('<90I', *words))
            return struct.pack('<90I', *words)

        boot = decode(packed())['fresh_io']
        self.assertEqual(boot['storage_boot']['booted_from'], 'storage file')
        self.assertEqual(boot['storage_boot']['reason'], 'matched')
        self.assertTrue(boot['passed'])
        words[48 + 30] = (3 << 16) | (2 << 8)
        boot = decode(packed())['fresh_io']
        self.assertEqual(
            (boot['storage_boot']['booted_from'], boot['storage_boot']['reason']), ('embedded fallback', 'CRC differs')
        )
        self.assertFalse(boot['passed'])

    def test_census_file_round_trip(self):
        from xy_forever.census import HEADER, MAGIC, parse, read

        words = [0] * 90
        words[:3] = [0x32525058, 1, 360]
        words[4:8] = [0, 1, 1, 1]
        words[11:15] = [0x53444358, 11, 0, 0]
        words[17] = 1  # role byte
        body = bytes(range(256)) * 4
        dump = bytearray(HEADER + 12 * 16) + body
        struct.pack_into('<3I', dump, HEADER, 0x11900000, len(body), HEADER + 12 * 16)
        struct.pack_into('<9I', dump, 0, MAGIC, 1, len(dump), 1, 1, 0, 5, 0, 0x88AA5588)
        struct.pack_into('<I', dump, 36, zlib.crc32(bytes(dump[HEADER:])))
        words[8] = len(dump)
        words[3] = zlib.crc32(struct.pack('<90I', *words))
        report, header, regions, raw = parse(struct.pack('<90I', *words) + bytes(dump))
        self.assertEqual(header['role'], 1)
        self.assertEqual(read(regions, raw, 0x11900004, 4), bytes([4, 5, 6, 7]))
        self.assertIsNone(read(regions, raw, 0x11A00000, 4))
        dump[HEADER + 12 * 16] ^= 1
        with self.assertRaisesRegex(ValueError, 'CRC'):
            parse(struct.pack('<90I', *words) + bytes(dump))

    def test_writer_fixture_is_never_a_storage_success(self):
        result = decode(report_only_fixture())
        self.assertTrue(result['writer_fixture_matched'])
        self.assertFalse(result['probe_passed'])
        self.assertEqual(result['begin'], 'unavailable')
        self.assertEqual(result['heap_result'], 'unavailable')

    def test_report_only_inner_image_has_only_exact_fixture_carry_write(self):
        stock = synthetic_stock()
        reporter = bytes(500)
        fixture = report_only_fixture()
        inner = inner_image(stock, reporter, fixture)
        checked = verify_inner(stock, inner, reporter, fixture)
        self.assertTrue(checked['writer_fixture_loaded'])
        self.assertFalse(checked['carry_range_never_written'])
        self.assertTrue(checked['exact_stock_reconstruction'])
        with self.assertRaises(ValueError):
            verify_inner(stock, inner, reporter)
        damaged = bytearray(inner)
        damaged[-17] ^= 1
        with self.assertRaises(ValueError):
            verify_inner(stock, bytes(damaged), reporter, fixture)

    def test_exact_reconstruction_and_carry_exclusion(self):
        stock = synthetic_stock()
        reporter = b'\0' * 500
        inner = inner_image(stock, reporter)
        result = verify_inner(stock, inner, reporter)
        self.assertTrue(result['exact_stock_reconstruction'])
        self.assertTrue(result['carry_range_never_written'])
        mutated = bytearray(inner)
        at = heap_record(stock).offset
        mutated[at : at + 16] = header(0x104, HEAP_BASE, HEAP_BYTES, 0)
        with self.assertRaises(ValueError):
            verify_inner(stock, bytes(mutated), reporter)
        mutated = bytearray(inner)
        mutated[-16:] = header(0x8004, 0x11A00002, 0, 0)
        with self.assertRaises(ValueError):
            verify_inner(stock, bytes(mutated), reporter)

    def test_reserved_reporter_limit_and_stock_preimages(self):
        stock = synthetic_stock()
        for reporter in (b'', bytes(CARRY - REPORTER + 1)):
            with self.assertRaises(ValueError):
                inner_image(stock, reporter)
        damaged = bytearray(stock)
        damaged[-20] ^= 1
        with self.assertRaises(ValueError):
            verify_inner(stock, bytes(damaged), bytes(20))
        self.assertEqual(END - CARRY, 4096)

    def test_report_decoder_integrity_and_failed_probe(self):
        words = [0] * 90
        words[:3] = [0x32525058, 1, 360]
        words[4:11] = [0, 0, 0, 1, 512, 512, 0]
        words[11:13] = [0x53444358, 1]
        words[13] = 5

        def pack():
            words[3] = 0
            raw = struct.pack('<90I', *words)
            words[3] = zlib.crc32(raw)
            return struct.pack('<90I', *words)

        raw = pack()
        self.assertTrue(decode(raw)['probe_passed'])
        for offset in range(len(raw)):
            damaged = bytearray(raw)
            damaged[offset] ^= 1
            with self.assertRaises(ValueError):
                decode(bytes(damaged))
        for offset, value in (
            (4, 5),
            (5, 3),
            (6, 12),
            (7, 0),
            (8, 511),
            (9, 0),
            (10, 1),
            (13, 6),
            (14, 1),
            (15, 1),
            (16, 1),
            (17, 1),
            (79, 4),
        ):
            original = words[offset]
            words[offset] = value
            self.assertFalse(decode(pack())['probe_passed'])
            words[offset] = original

    def test_native_report_accepts_the_report_only_fixture(self):
        # The C++ reader's checksum and fields (tests/native/storage_probe_record_test.cpp) agree with the writer's.
        rel = 'tests/native/storage_probe_record_test.cpp'
        xyf_hosttest.used(rel)
        with tempfile.TemporaryDirectory(prefix='xyf-probe-record-') as temporary:
            fixture = Path(temporary) / 'fixture.bin'
            fixture.write_bytes(report_only_fixture())
            run = subprocess.run(
                [
                    str(xyf_hosttest.DOCTEST),
                    '--source-file=*' + rel,
                    '--test-case=the report-only fixture',
                    '--no-skip',
                    '--no-intro',
                    '--no-version',
                ],
                capture_output=True,
                text=True,
                timeout=20,
                env=dict(os.environ, XYF_PROBE_FIXTURE=str(fixture)),
            )
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertRegex(run.stdout, r'test cases: +1 \| +1 passed')


if __name__ == '__main__':
    unittest.main()
