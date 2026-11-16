import struct
import unittest

from xy_forever.companions import (
    CompanionError, CyacdImage, CypressSession, NordicApplication,
    backplane_program_steps, ble_start_packet, crc16_ccitt,
    cypress_packet, cypress_response,
)


def cyacd(rows=None, silicon=0x100A11A0):
    if rows is None:
        rows = [(0, row, bytes((row & 255,)) * 256)
                for row in list(range(25, 391)) + [511]]
    lines = ["%08X0000" % silicon]
    for array, number, data in rows:
        payload = bytes((array,)) + struct.pack(">HH", number, len(data)) + data
        lines.append(":" + (payload + bytes(((-sum(payload)) & 255,))).hex())
    return ("\r\n".join(lines) + "\r\n").encode()


def response(payload=b"", status=0):
    # Independent framing used by a simulated chip, not cypress_packet().
    frame = bytes((1, status, len(payload) & 255, len(payload) >> 8)) + payload
    checksum = 0x10000 - sum(frame)
    return frame + bytes((checksum & 255, (checksum >> 8) & 255, 0x17))


def enter_response(silicon=0x100A11A0, revision=0, version=0x010203):
    return response(struct.pack("<IB", silicon, revision) + version.to_bytes(3, "little"))


class CompanionImageTests(unittest.TestCase):
    def test_cyacd_validates_every_row_and_observed_layout(self):
        image = CyacdImage.parse(cyacd())
        image.require_opxy_layout()
        self.assertEqual(len(image.rows), 367)
        self.assertEqual(image.rows[-1].number, 511)

    def test_cyacd_corruption_rejected_before_a_plan_exists(self):
        damaged = bytearray(cyacd())
        damaged[-5] = ord("0") if damaged[-5] != ord("0") else ord("1")
        with self.assertRaisesRegex(CompanionError, "checksum"):
            CyacdImage.parse(bytes(damaged))

    def test_cyacd_rejects_duplicate_rows_and_malformed_inputs(self):
        duplicated = [(0, 25, b"data"), (0, 25, b"data")]
        for data in (cyacd(duplicated), b"", b"100A11A00000\n:0000010001ff\n",
                     b"100A11A00001\n", b"100A11A00000\n:", b"\xff",
                     b"100A11A00000\n:00 00\n"):
            with self.subTest(data=data[:30]):
                with self.assertRaises(CompanionError):
                    CyacdImage.parse(data)

    def test_bootloader_rows_and_wrong_silicon_cannot_be_planned(self):
        for data in (cyacd(silicon=0x100A11A1),
                     cyacd([(0, 0, bytes(256))]),
                     cyacd([(1, 25, bytes(256))])):
            image = CyacdImage.parse(data)
            with self.assertRaises(CompanionError):
                backplane_program_steps(image, 64)

    def test_comment_lines_are_accepted(self):
        image = CyacdImage.parse(b"# generated image\n" + cyacd())
        image.require_opxy_layout()

    def test_ble_vector_checks_and_stock_length_bound(self):
        for data in (b"", bytes(8), struct.pack("<II", 0x20000102, 0x19329),
                     struct.pack("<II", 0x20001000, 0x19328),
                     struct.pack("<II", 0x2000FFC0, 0x4541),
                     struct.pack("<II", 0x2000FFC0, 0x953D),
                     struct.pack("<II", 0x20001000, 0x19329) + bytes(0x70000)):
            with self.subTest(length=len(data)):
                with self.assertRaises(CompanionError):
                    NordicApplication.parse(data)


class CypressProtocolTests(unittest.TestCase):
    def setUp(self):
        self.image = CyacdImage.parse(cyacd())

    def test_enter_packet_matches_stock_known_bytes(self):
        self.assertEqual(cypress_packet(0x38).hex(), "01380000c7ff17")
        self.assertEqual(cypress_packet(0x31).hex(), "01310000ceff17")
        self.assertEqual(cypress_packet(0x3B).hex(), "013b0000c4ff17")

    def test_response_length_checksum_and_error_status_are_checked(self):
        good = response(b"abc")
        self.assertEqual(cypress_response(good, 3), b"abc")
        for packet, expected in ((good + b"x", 3), (good, 2),
                                 (good[:-2] + b"\x00\x17", 3),
                                 (response(status=5), 0), (b"", 0)):
            with self.assertRaises(CompanionError):
                cypress_response(packet, expected)

    def test_silicon_mismatch_stops_before_programming(self):
        session = CypressSession(self.image, 64)
        self.assertEqual(session.request()[1], 0x38)
        with self.assertRaises(CompanionError):
            session.exit_request()
        with self.assertRaisesRegex(CompanionError, "identity"):
            session.accept(enter_response(silicon=1))
        self.assertTrue(session.failed)
        with self.assertRaises(CompanionError):
            session.request()

    def test_device_flash_bounds_are_checked_before_programming(self):
        session = CypressSession(self.image, 64)
        session.accept(enter_response())
        self.assertEqual(session.request(), cypress_packet(0x32, b"\0"))
        with self.assertRaisesRegex(CompanionError, "bounds"):
            session.accept(response(struct.pack("<HH", 0, 400)))
        with self.assertRaises(CompanionError):
            session.request()

    def test_verification_failure_stops_without_next_row_or_exit(self):
        session = CypressSession(self.image, 512)
        session.accept(enter_response())
        session.accept(response(struct.pack("<HH", 0, 511)))
        self.assertEqual(session.request()[1], 0x39)
        session.accept(response())
        self.assertEqual(session.request()[1], 0x3A)
        with self.assertRaisesRegex(CompanionError, "verification"):
            session.accept(response(b"\x01"))
        self.assertTrue(session.failed)

    def test_full_plan_replays_to_exact_application_rows(self):
        # Model the chip's buffer/program/verify operations independently. This
        # exercises small bridge MTUs, endianness, row checksums and finalization.
        for mtu in (11, 32, 64, 255, 266, 512):
            session = CypressSession(self.image, mtu)
            session.accept(enter_response())
            session.accept(response(struct.pack("<HH", 0, 511)))
            memory = {}
            buffer = b""
            while not session.finished:
                packet = session.request()
                self.assertLessEqual(len(packet), mtu)
                command = packet[1]
                payload = packet[4:-3]
                if command == 0x37:
                    buffer += payload
                    reply = response()
                elif command == 0x39:
                    array = payload[0]
                    row = int.from_bytes(payload[1:3], "little")
                    self.assertNotIn((array, row), memory)
                    memory[array, row] = buffer + payload[3:]
                    buffer = b""
                    reply = response()
                elif command == 0x3A:
                    identity = (payload[0], int.from_bytes(payload[1:], "little"))
                    reply = response(bytes(((-sum(memory[identity])) & 255,)))
                else:
                    self.assertEqual(command, 0x31)
                    self.assertEqual(len(memory), 367)
                    reply = response(b"\x01")
                session.accept(reply)
            self.assertEqual(memory, {(row.array_id, row.number): row.data for row in self.image.rows})
            self.assertEqual(session.exit_request(), bytes.fromhex("013b0000c4ff17"))
            with self.assertRaises(CompanionError):
                session.request()


class NordicProtocolTests(unittest.TestCase):
    def setUp(self):
        self.data = struct.pack("<II", 0x20012FD0, 0x190D1) + bytes(range(256)) * 3
        self.image = NordicApplication.parse(self.data)

    def test_crc_standard_vector_and_incremental_calls(self):
        self.assertEqual(crc16_ccitt(b"123456789"), 0x31C3)
        self.assertEqual(crc16_ccitt(b""), 0)
        self.assertEqual(crc16_ccitt(self.data[8:], crc16_ccitt(self.data[:8])), self.image.checksum)

    def test_header_and_start_metadata_have_stock_field_order(self):
        version = (1, 2, 3, 4)
        header = self.image.transfer_header(version, 0x12345678)
        self.assertEqual(len(header), 64)
        self.assertEqual(header[:5], bytes.fromhex("beefcafe03"))
        self.assertEqual(header[5:9], len(self.data).to_bytes(4, "big"))
        # Decision 017 (1.2.1 bootloader): CRC-16 at 9-10, version at 11-18.
        self.assertEqual(header[9:11], self.image.checksum.to_bytes(2, "big"))
        self.assertEqual(header[11:19], bytes.fromhex("0001000200030004"))
        self.assertEqual(header[19:27], bytes.fromhex("0001f00012345678"))
        self.assertEqual(header[27:], bytes(37))
        self.assertEqual(ble_start_packet(version, 0xB0, 0x12345678),
                         bytes.fromhex("020001000200030004b012345678"))

    def test_bridge_chunks_reconstruct_image_with_stock_limit(self):
        packets = self.image.data_packets()
        self.assertTrue(all(packet[0] == 3 and 1 < len(packet) <= 249 for packet in packets))
        self.assertEqual(b"".join(packet[1:] for packet in packets), self.data)
        self.assertEqual(len(packets[-1]) - 1, len(self.data) % 248)

    def test_invalid_metadata_does_not_wrap_or_truncate(self):
        for version, device in (((1, 2, 3), 0), ((1, 2, 3, 65536), 0),
                                ((1, 2, 3, 4), -1), ((1, 2, 3, 4), 0x100000000)):
            with self.assertRaises(CompanionError):
                self.image.transfer_header(version, device)


if __name__ == "__main__":
    unittest.main()
