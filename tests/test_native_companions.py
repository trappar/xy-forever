"""Compare the freestanding native codecs/state machine to the Python model."""

import random
import unittest

import pytest
import xyf_hosttest
from test_companions import cyacd, enter_response, response

from xy_forever.companions import (
    CyacdImage,
    CypressSession,
    NordicApplication,
    ble_start_packet,
    crc16_ccitt,
    cypress_packet,
)


@pytest.mark.gate
class NativeCompanionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lib = xyf_hosttest.module('companions')

    def output(self, method, *args, capacity=512):
        # Guard bytes are checked after every call, including rejected calls.
        status, count, backing = method(*args, capacity=capacity)
        self.assertEqual(backing[:16], b'\xa5' * 16)
        self.assertEqual(backing[16 + capacity :], b'\xa5' * 16)
        if status:
            self.assertEqual(count, 0)
            self.assertEqual(backing[16 : 16 + capacity], b'\xa5' * capacity)
            return status, b''
        self.assertLessEqual(count, capacity)
        return status, backing[16 : 16 + count]

    def test_cypress_all_commands_and_payload_boundaries_match_python(self):
        for command in range(256):
            for size in (0, 1, 3, 8, 248, 256, 505):
                data = bytes((i * 37 + command) & 255 for i in range(size))
                status, encoded = self.output(self.lib.encode, command, data, size)
                self.assertEqual(status, 0)
                self.assertEqual(encoded, cypress_packet(command, data))

    def test_capacity_and_invalid_arguments_never_write_output(self):
        for capacity in (0, 1, 6, 13):
            self.assertNotEqual(self.output(self.lib.encode, 0x38, bytes(8), 8, capacity=capacity)[0], 0)
        for command, size, data in ((256, 0, None), (0, 506, bytes(506)), (0x38, 1, None)):
            self.assertNotEqual(self.output(self.lib.encode, command, data, size)[0], 0)

    def test_response_validation_and_borrowed_payload(self):
        for payload in (b'', b'\xff', bytes(range(256))):
            encoded = response(payload)
            status, offset = self.lib.decode(encoded, len(payload))
            self.assertEqual(status, 0)
            self.assertEqual(encoded[offset : offset + len(payload)], payload)
            self.assertEqual(offset, 4)
        valid = response(b'xyz')
        malformed = [
            b'',
            valid + b'x',
            valid[:-1],
            b'\0' + valid[1:],
            valid[:-3] + b'\0\0\x17',
            response(b'xyz', status=5),
        ]
        for packet in malformed:
            status, offset = self.lib.decode(packet, 3)
            self.assertNotEqual(status, 0)
            self.assertIsNone(offset)

    def test_crc_matches_python_across_initial_values_and_lengths(self):
        rng = random.Random(0xBEEFCAFE)
        for size in (0, 1, 2, 17, 248, 4096, 65536):
            data = bytes(rng.randrange(256) for _ in range(size))
            for initial in (0, 1, 0xFFFF, 0x1234):
                self.assertEqual(self.lib.crc(data, size, initial), (0, crc16_ccitt(data, initial)))
        status, out = self.lib.crc(None, 1, 0)
        self.assertNotEqual(status, 0)
        self.assertEqual(out, 0)

    def test_ble_headers_and_start_match_python_exactly(self):
        image = NordicApplication.parse(bytes.fromhex('d02f0120d1900100') + bytes(range(256)))
        for version in ((0, 0, 0, 0), (1, 2, 3, 4), (65535, 4, 65535, 7)):
            fields = version
            for device in (0, 0x12345678, 0xFFFFFFFF):
                status, data = self.output(self.lib.ble_header, len(image.data), fields, image.checksum, device)
                self.assertEqual(status, 0)
                self.assertEqual(data, image.transfer_header(version, device))
                status, data = self.output(self.lib.ble_start, fields, 0xB0, device)
                self.assertEqual(status, 0)
                self.assertEqual(data, ble_start_packet(version, 0xB0, device))
        self.assertNotEqual(
            self.output(self.lib.ble_header, len(image.data), fields, image.checksum, 0, capacity=63)[0], 0
        )
        self.assertNotEqual(self.output(self.lib.ble_start, fields, 0xB0, 0, capacity=13)[0], 0)
        bad = (1, 2, 3, 65536)
        self.assertNotEqual(self.output(self.lib.ble_header, len(image.data), bad, 0, 0)[0], 0)

    def test_ble_chunks_reconstruct_image_and_preserve_buffer_guards(self):
        for size in (8, 248, 252, 4096, 0x70000):
            image = bytes((i * 17) & 255 for i in range(size))
            chunks = []
            offset = 0
            while offset < size:
                status, produced, consumed, backing = self.lib.ble_chunk(image, size, offset, 249)
                self.assertEqual(status, 0)
                self.assertEqual(backing[:16] + backing[-16:], b'\xa5' * 32)
                packet = backing[16 : 16 + produced]
                self.assertEqual(packet[0], 3)
                chunks.append(packet[1:])
                self.assertEqual(consumed + 1, produced)
                offset += consumed
            self.assertEqual(b''.join(chunks), image)

    def test_ble_chunk_rejection_keeps_buffer_and_counts_untouched(self):
        source = bytes(256)
        for size, offset, capacity in (
            (256, 0, 248),
            (256, 256, 249),
            (256, 0xFFFFFFFF, 249),
            (7, 0, 249),
            (0x70004, 0, 249),
        ):
            status, produced, consumed, output = self.lib.ble_chunk(source, size, offset, capacity)
            self.assertNotEqual(status, 0)
            self.assertEqual(produced, 0)
            self.assertEqual(consumed, 0)
            self.assertEqual(output, b'\xa5' * (capacity + 32))

    def session(self, mtu=64):
        image = CyacdImage.parse(cyacd())
        rows = [[row.array_id, row.number, bytes(row.data)] for row in image.rows]
        session = self.lib.Session()
        self.assertEqual(session.init([tuple(row) for row in rows], mtu), 0)
        return image, rows, session

    def test_native_session_matches_every_python_request_and_verified_exit(self):
        for mtu in (11, 32, 64, 255, 266, 512):
            image, rows, native = self.session(mtu)
            golden = CypressSession(image, mtu)
            self.assertNotEqual(self.output(native.exit)[0], 0)
            while not golden.finished:
                expected = golden.request()
                status, actual = self.output(native.request)
                self.assertEqual(status, 0)
                self.assertEqual(actual, expected)
                if golden.position == -2:
                    reply = enter_response()
                elif golden.position == -1:
                    reply = response(bytes.fromhex('0000ff01'))
                else:
                    step = golden.steps[golden.position]
                    reply = response(
                        step.expected_payload if step.expected_payload else b'\1' if step.expect_nonzero else b''
                    )
                golden.accept(reply)
                self.assertEqual(native.accept(reply), 0)
            self.assertEqual(native.phase(), 6)
            self.assertEqual(self.output(native.exit), (0, golden.exit_request()))
            self.assertNotEqual(self.output(native.request)[0], 0)

    def test_identity_bounds_and_row_corruption_stop_native_session(self):
        for replies in (
            (enter_response(silicon=0),),
            (enter_response(), response(bytes.fromhex('00000001'))),
            (enter_response(), response(bytes.fromhex('0000ff01')), response(), response(b'\1')),
        ):
            image, rows, native = self.session(512)
            results = [native.accept(reply) for reply in replies]
            self.assertNotEqual(results[-1], 0)
            self.assertEqual(native.phase(), 7)
            self.assertNotEqual(self.output(native.request)[0], 0)
            self.assertNotEqual(self.output(native.exit)[0], 0)

    def test_incorrect_admitted_row_layout_cannot_initialize_native_session(self):
        image, rows, native = self.session()
        rows[0][1] = 0
        self.assertNotEqual(native.init([tuple(row) for row in rows], 64), 0)
        self.assertNotEqual(self.output(native.request)[0], 0)

    def test_default_constructed_session_stops_before_image_initialization(self):
        storage = self.lib.Session()  # constructed over 0xA5 bytes
        self.assertEqual(storage.phase(), 7)
        self.assertNotEqual(self.output(storage.request)[0], 0)
        self.assertNotEqual(self.output(storage.exit)[0], 0)
        reply = response()
        self.assertNotEqual(storage.accept(reply), 0)
        self.assertEqual(storage.phase(), 7)


if __name__ == '__main__':
    unittest.main()
