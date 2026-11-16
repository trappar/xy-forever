"""Compare the freestanding native codecs/state machine to the Python model."""

import ctypes
from pathlib import Path
import random
import tempfile
import unittest

from xy_forever import hostbuild
from xy_forever.companions import (
    CyacdImage, CypressSession, NordicApplication, ble_start_packet,
    crc16_ccitt, cypress_packet,
)
from test_companions import cyacd, enter_response, response


U32 = ctypes.c_uint
BYTE = ctypes.c_ubyte
PTR = ctypes.POINTER(BYTE)
VOID = ctypes.c_void_p
UPTR = ctypes.POINTER(U32)


class Row(ctypes.Structure):
    _fields_ = [("array_id", U32), ("number", U32), ("data", PTR), ("size", U32)]


def byte_buffer(data):
    return (BYTE * len(data)).from_buffer_copy(data)


class NativeCompanionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="xyf-companion-native-")
        root = Path(__file__).resolve().parents[1]
        shared = Path(cls.temporary.name) / "companions.dylib"
        cls.command = ["/usr/bin/clang++", "-std=c++11", "-shared", "-fPIC", "-O2",
                       "-Wall", "-Wextra", "-Werror", "-ffreestanding", "-fno-builtin",
                       "-fno-stack-protector", "-fno-exceptions", "-fno-rtti", str(root / "native/companions.cpp"),
                       str(root / "tests/companion_bridge.cpp"), "-o", str(shared)]
        hostbuild.run(cls.command, check=True, capture_output=True)
        cls.lib = ctypes.CDLL(str(shared))
        signatures = {
            "encode": [U32, PTR, U32, PTR, U32, UPTR],
            "decode": [PTR, U32, U32, ctypes.POINTER(PTR)],
            "crc": [PTR, U32, U32, UPTR],
            "ble_header": [U32, UPTR, U32, U32, PTR, U32, UPTR],
            "ble_start": [UPTR, U32, U32, PTR, U32, UPTR],
            "ble_chunk": [PTR, U32, U32, PTR, U32, UPTR, UPTR],
            "session_init": [VOID, ctypes.POINTER(Row), U32, U32],
            "session_construct": [VOID],
            "session_request": [VOID, PTR, U32, UPTR],
            "session_accept": [VOID, PTR, U32],
            "session_exit": [VOID, PTR, U32, UPTR],
            "session_phase": [VOID],
        }
        for name, args in signatures.items():
            method = getattr(cls.lib, "companion_" + name)
            method.argtypes = args
            method.restype = None if name == "session_construct" else ctypes.c_int
        cls.lib.companion_session_size.restype = U32

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def output(self, method, *args, capacity=512):
        # Guard bytes are checked after every call, including rejected calls.
        backing = (BYTE * (capacity + 32))(*([0xA5] * (capacity + 32)))
        out = ctypes.cast(ctypes.byref(backing, 16), PTR)
        count = U32(0xFFFFFFFF)
        status = method(*args, out, capacity, ctypes.byref(count))
        self.assertEqual(bytes(backing[:16]), b"\xA5" * 16)
        self.assertEqual(bytes(backing[16 + capacity:]), b"\xA5" * 16)
        if status:
            self.assertEqual(count.value, 0)
            self.assertEqual(bytes(backing[16:16 + capacity]), b"\xA5" * capacity)
            return status, b""
        self.assertLessEqual(count.value, capacity)
        return status, bytes(backing[16:16 + count.value])

    def test_cypress_all_commands_and_payload_boundaries_match_python(self):
        for command in range(256):
            for size in (0, 1, 3, 8, 248, 256, 505):
                data = bytes((i * 37 + command) & 255 for i in range(size))
                status, encoded = self.output(self.lib.companion_encode, command, byte_buffer(data), size)
                self.assertEqual(status, 0)
                self.assertEqual(encoded, cypress_packet(command, data))

    def test_capacity_and_invalid_arguments_never_write_output(self):
        for capacity in (0, 1, 6, 13):
            self.assertNotEqual(self.output(self.lib.companion_encode, 0x38,
                                           byte_buffer(bytes(8)), 8, capacity=capacity)[0], 0)
        for command, size, data in ((256, 0, None), (0, 506, byte_buffer(bytes(506))),
                                    (0x38, 1, None)):
            self.assertNotEqual(self.output(self.lib.companion_encode, command, data, size)[0], 0)

    def test_response_validation_and_borrowed_payload(self):
        for payload in (b"", b"\xFF", bytes(range(256))):
            encoded = response(payload)
            data = byte_buffer(encoded)
            out = PTR()
            self.assertEqual(self.lib.companion_decode(data, len(encoded), len(payload), ctypes.byref(out)), 0)
            self.assertEqual(ctypes.string_at(out, len(payload)), payload)
            self.assertEqual(ctypes.cast(out, VOID).value, ctypes.addressof(data) + 4)
        valid = response(b"xyz")
        malformed = [b"", valid + b"x", valid[:-1], b"\0" + valid[1:],
                     valid[:-3] + b"\0\0\x17", response(b"xyz", status=5)]
        for packet in malformed:
            out = ctypes.cast(VOID(1), PTR)
            self.assertNotEqual(self.lib.companion_decode(byte_buffer(packet), len(packet), 3, ctypes.byref(out)), 0)
            self.assertFalse(out)

    def test_crc_matches_python_across_initial_values_and_lengths(self):
        rng = random.Random(0xBEEFCAFE)
        for size in (0, 1, 2, 17, 248, 4096, 65536):
            data = bytes(rng.randrange(256) for _ in range(size))
            for initial in (0, 1, 0xFFFF, 0x1234):
                out = U32()
                self.assertEqual(self.lib.companion_crc(byte_buffer(data), size, initial, ctypes.byref(out)), 0)
                self.assertEqual(out.value, crc16_ccitt(data, initial))
        out = U32(1)
        self.assertNotEqual(self.lib.companion_crc(None, 1, 0, ctypes.byref(out)), 0)
        self.assertEqual(out.value, 0)

    def test_ble_headers_and_start_match_python_exactly(self):
        image = NordicApplication.parse(bytes.fromhex("d02f0120d1900100") + bytes(range(256)))
        for version in ((0, 0, 0, 0), (1, 2, 3, 4), (65535, 4, 65535, 7)):
            fields = (U32 * 4)(*version)
            for device in (0, 0x12345678, 0xFFFFFFFF):
                status, data = self.output(self.lib.companion_ble_header, len(image.data), fields,
                                           image.checksum, device)
                self.assertEqual(status, 0)
                self.assertEqual(data, image.transfer_header(version, device))
                status, data = self.output(self.lib.companion_ble_start, fields, 0xB0, device)
                self.assertEqual(status, 0)
                self.assertEqual(data, ble_start_packet(version, 0xB0, device))
        self.assertNotEqual(self.output(self.lib.companion_ble_header, len(image.data), fields,
                                       image.checksum, 0, capacity=63)[0], 0)
        self.assertNotEqual(self.output(self.lib.companion_ble_start, fields, 0xB0, 0, capacity=13)[0], 0)
        bad = (U32 * 4)(1, 2, 3, 65536)
        self.assertNotEqual(self.output(self.lib.companion_ble_header, len(image.data), bad, 0, 0)[0], 0)

    def test_ble_chunks_reconstruct_image_and_preserve_buffer_guards(self):
        for size in (8, 248, 252, 4096, 0x70000):
            image = bytes((i * 17) & 255 for i in range(size))
            source = byte_buffer(image)
            chunks = []
            offset = 0
            while offset < size:
                backing = (BYTE * 281)(*([0xA5] * 281))
                out = ctypes.cast(ctypes.byref(backing, 16), PTR)
                produced, consumed = U32(), U32()
                self.assertEqual(self.lib.companion_ble_chunk(source, size, offset, out, 249,
                                                               ctypes.byref(produced), ctypes.byref(consumed)), 0)
                self.assertEqual(bytes(backing[:16]) + bytes(backing[-16:]), b"\xA5" * 32)
                packet = bytes(backing[16:16 + produced.value])
                self.assertEqual(packet[0], 3)
                chunks.append(packet[1:])
                self.assertEqual(consumed.value + 1, produced.value)
                offset += consumed.value
            self.assertEqual(b"".join(chunks), image)

    def test_ble_chunk_rejection_keeps_buffer_and_counts_untouched(self):
        source = byte_buffer(bytes(256))
        for size, offset, capacity in ((256, 0, 248), (256, 256, 249),
                                       (256, 0xFFFFFFFF, 249), (7, 0, 249),
                                       (0x70004, 0, 249)):
            output = (BYTE * 281)(*([0xA5] * 281))
            produced, consumed = U32(123), U32(456)
            self.assertNotEqual(self.lib.companion_ble_chunk(source, size, offset, output, capacity,
                                                            ctypes.byref(produced), ctypes.byref(consumed)), 0)
            self.assertEqual(produced.value, 0)
            self.assertEqual(consumed.value, 0)
            self.assertEqual(bytes(output), b"\xA5" * 281)

    def session(self, mtu=64):
        image = CyacdImage.parse(cyacd())
        data = [byte_buffer(row.data) for row in image.rows]
        rows = (Row * len(image.rows))(*(Row(row.array_id, row.number, ptr, len(row.data))
                                       for row, ptr in zip(image.rows, data)))
        storage = ctypes.create_string_buffer(self.lib.companion_session_size())
        self.assertEqual(self.lib.companion_session_init(storage, rows, len(rows), mtu), 0)
        return image, data, rows, storage

    def test_native_session_matches_every_python_request_and_verified_exit(self):
        for mtu in (11, 32, 64, 255, 266, 512):
            image, keepalive, rows, native = self.session(mtu)
            golden = CypressSession(image, mtu)
            self.assertNotEqual(self.output(self.lib.companion_session_exit, native)[0], 0)
            while not golden.finished:
                expected = golden.request()
                status, actual = self.output(self.lib.companion_session_request, native)
                self.assertEqual(status, 0)
                self.assertEqual(actual, expected)
                if golden.position == -2:
                    reply = enter_response()
                elif golden.position == -1:
                    reply = response(bytes.fromhex("0000ff01"))
                else:
                    step = golden.steps[golden.position]
                    reply = response(step.expected_payload if step.expected_payload else
                                     b"\1" if step.expect_nonzero else b"")
                golden.accept(reply)
                self.assertEqual(self.lib.companion_session_accept(native, byte_buffer(reply), len(reply)), 0)
            self.assertEqual(self.lib.companion_session_phase(native), 6)
            self.assertEqual(self.output(self.lib.companion_session_exit, native), (0, golden.exit_request()))
            self.assertNotEqual(self.output(self.lib.companion_session_request, native)[0], 0)

    def test_identity_bounds_and_row_corruption_stop_native_session(self):
        for replies in ((enter_response(silicon=0),),
                        (enter_response(), response(bytes.fromhex("00000001"))),
                        (enter_response(), response(bytes.fromhex("0000ff01")), response(), response(b"\1"))):
            image, keepalive, rows, native = self.session(512)
            results = [self.lib.companion_session_accept(native, byte_buffer(reply), len(reply)) for reply in replies]
            self.assertNotEqual(results[-1], 0)
            self.assertEqual(self.lib.companion_session_phase(native), 7)
            self.assertNotEqual(self.output(self.lib.companion_session_request, native)[0], 0)
            self.assertNotEqual(self.output(self.lib.companion_session_exit, native)[0], 0)

    def test_incorrect_admitted_row_layout_cannot_initialize_native_session(self):
        image, keepalive, rows, native = self.session()
        rows[0].number = 0
        self.assertNotEqual(self.lib.companion_session_init(native, rows, len(rows), 64), 0)
        self.assertNotEqual(self.output(self.lib.companion_session_request, native)[0], 0)

    def test_default_constructed_session_stops_before_image_initialization(self):
        storage = ctypes.create_string_buffer(b"\xA5" * self.lib.companion_session_size())
        self.lib.companion_session_construct(storage)
        self.assertEqual(self.lib.companion_session_phase(storage), 7)
        self.assertNotEqual(self.output(self.lib.companion_session_request, storage)[0], 0)
        self.assertNotEqual(self.output(self.lib.companion_session_exit, storage)[0], 0)
        reply = response()
        self.assertNotEqual(self.lib.companion_session_accept(storage, byte_buffer(reply), len(reply)), 0)
        self.assertEqual(self.lib.companion_session_phase(storage), 7)


if __name__ == "__main__":
    unittest.main()
