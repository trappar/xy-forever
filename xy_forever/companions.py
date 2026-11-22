"""Offline companion-image validation and stock protocol packet planning.

These helpers never open a device or flash anything. Native bridge drivers,
power-loss recovery and runtime entry into each bootloader require device proof.
The Cypress (backplane) details come from the 1.2.5 listings; the Bluetooth
update session follows the 1.2.1 Nordic bootloader (native/ble_session.hpp).
"""

import binascii
from dataclasses import dataclass
from hashlib import sha256
import re
import struct
from typing import Tuple


class CompanionError(ValueError):
    pass


@dataclass(frozen=True)
class CyacdRow:
    array_id: int
    number: int
    data: bytes
    file_checksum: int

    @property
    def device_checksum(self) -> int:
        # Stock adds the CYACD framing fields back to its file checksum.
        return (-sum(self.data)) & 0xFF


@dataclass(frozen=True)
class CyacdImage:
    silicon_id: int
    silicon_revision: int
    checksum_type: int
    rows: Tuple[CyacdRow, ...]
    sha256: str

    @classmethod
    def parse(cls, data: bytes) -> "CyacdImage":
        if not 1 <= len(data) <= 0x30000:
            raise CompanionError("CYACD length exceeds the stock 0x30000-byte limit")
        try:
            lines = [line for line in data.decode("ascii").splitlines()
                     if line and not line.startswith("#")]
        except UnicodeDecodeError as exc:
            raise CompanionError("CYACD must be ASCII") from exc
        if not lines or re.fullmatch(r"[0-9a-fA-F]{12}", lines[0]) is None:
            raise CompanionError("CYACD header must contain six hexadecimal bytes")
        header = bytes.fromhex(lines[0])
        if header[5] != 0:
            raise CompanionError("only the observed sum-checksum CYACD format is supported")
        rows = []
        identities = set()
        for line in lines[1:]:
            if re.fullmatch(r":[0-9a-fA-F]+", line) is None or len(line) % 2 != 1:
                raise CompanionError("invalid CYACD row encoding")
            raw = bytes.fromhex(line[1:])
            if len(raw) < 7:
                raise CompanionError("truncated CYACD row")
            size = int.from_bytes(raw[3:5], "big")
            if not 1 <= size <= 800 or len(raw) != size + 6:
                raise CompanionError("CYACD row size mismatch or unsupported row length")
            if sum(raw) & 0xFF:
                raise CompanionError("CYACD row checksum mismatch")
            identity = (raw[0], int.from_bytes(raw[1:3], "big"))
            if identity in identities:
                raise CompanionError("duplicate CYACD row")
            identities.add(identity)
            rows.append(CyacdRow(identity[0], identity[1], raw[5:-1], raw[-1]))
        if not rows:
            raise CompanionError("CYACD contains no flash rows")
        return cls(int.from_bytes(header[:4], "big"), header[4], header[5],
                   tuple(rows), sha256(data).hexdigest())

    def require_opxy_layout(self) -> None:
        """Constrain writes to the exact application layout seen in both releases."""
        if self.silicon_id != 0x100A11A0 or self.silicon_revision != 0:
            raise CompanionError("CYACD does not target the observed OP-XY backplane")
        expected = [(0, row) for row in range(25, 391)] + [(0, 511)]
        actual = [(row.array_id, row.number) for row in self.rows]
        if actual != expected or any(len(row.data) != 256 for row in self.rows):
            raise CompanionError("CYACD differs from the observed OP-XY application row layout")


def cypress_packet(command: int, payload: bytes = b"") -> bytes:
    """Encode stock checksum mode 0; this is not the bridge transport envelope."""
    if not 0 <= command <= 255 or len(payload) > 505:
        raise CompanionError("Cypress packet exceeds stock packet-buffer bounds")
    prefix = struct.pack("<BBH", 1, command, len(payload)) + payload
    return prefix + struct.pack("<H", (-sum(prefix)) & 0xFFFF) + b"\x17"


def cypress_response(packet: bytes, expected_length: int) -> bytes:
    if len(packet) < 7 or packet[0] != 1 or packet[-1] != 0x17:
        raise CompanionError("invalid Cypress response framing")
    size = int.from_bytes(packet[2:4], "little")
    if size != expected_length or len(packet) != size + 7:
        raise CompanionError("Cypress response length mismatch")
    if int.from_bytes(packet[-3:-1], "little") != (-sum(packet[:-3])) & 0xFFFF:
        raise CompanionError("Cypress response checksum mismatch")
    if packet[1] != 0:
        raise CompanionError("Cypress bootloader returned status 0x%02x" % packet[1])
    return packet[4:-3]


@dataclass(frozen=True)
class CypressStep:
    label: str
    packet: bytes
    response_length: int
    expected_payload: bytes = b""
    expect_nonzero: bool = False


def backplane_program_steps(image: CyacdImage, transfer_size: int) -> Tuple[CypressStep, ...]:
    """Build row transactions after device identity and bounds have been checked.

    transfer_size is the bridge callback's maximum *whole packet* size, not an
    assumed I2C/UART MTU. The native driver must establish it before use.
    """
    image.require_opxy_layout()
    if not 11 <= transfer_size <= 512:
        raise CompanionError("invalid Cypress transport packet limit")
    steps = []
    for row in image.rows:
        prefix = bytes((row.array_id,)) + struct.pack("<H", row.number)
        offset = 0
        while len(row.data) - offset > transfer_size - 10:
            count = min(len(row.data) - offset, transfer_size - 7)
            steps.append(CypressStep("buffer row %d" % row.number,
                                     cypress_packet(0x37, row.data[offset:offset + count]), 0))
            offset += count
        steps.append(CypressStep("program row %d" % row.number,
                                 cypress_packet(0x39, prefix + row.data[offset:]), 0))
        steps.append(CypressStep("verify row %d" % row.number,
                                 cypress_packet(0x3A, prefix), 1,
                                 bytes((row.device_checksum,))))
    steps.append(CypressStep("verify application", cypress_packet(0x31), 1, expect_nonzero=True))
    return tuple(steps)


class CypressSession:
    """Deterministic request/response state, with no transport or automatic retry.

    The caller must enter the chip bootloader and verify recovery separately.
    Any malformed, failed or mismatched response stops before the next request.
    An interrupted row is intentionally not retried without a new device probe.
    """

    def __init__(self, image: CyacdImage, transfer_size: int):
        image.require_opxy_layout()
        self.image = image
        self.transfer_size = transfer_size
        self.steps = backplane_program_steps(image, transfer_size)
        self.position = -2
        self.failed = False
        self.finished = False

    def request(self) -> bytes:
        if self.failed or self.finished:
            raise CompanionError("Cypress session has stopped")
        if self.position == -2:
            return cypress_packet(0x38)
        if self.position == -1:
            return cypress_packet(0x32, b"\0")
        return self.steps[self.position].packet

    def accept(self, response: bytes) -> None:
        if self.failed or self.finished:
            raise CompanionError("Cypress session has stopped")
        try:
            if self.position == -2:
                payload = cypress_response(response, 8)
                silicon = int.from_bytes(payload[:4], "little")
                version = int.from_bytes(payload[5:8], "little")
                if (silicon != self.image.silicon_id or payload[4] != self.image.silicon_revision
                        or version & 0xFF0000 != 0x010000):
                    raise CompanionError("Cypress device identity or bootloader version mismatch")
                self.position = -1
            elif self.position == -1:
                payload = cypress_response(response, 4)
                first, last = struct.unpack("<HH", payload)
                if first > last or any(not first <= row.number <= last for row in self.image.rows):
                    raise CompanionError("Cypress application rows exceed reported device bounds")
                self.position = 0
            else:
                step = self.steps[self.position]
                payload = cypress_response(response, step.response_length)
                if step.expected_payload and payload != step.expected_payload:
                    raise CompanionError("Cypress verification failed: " + step.label)
                if step.expect_nonzero and not any(payload):
                    raise CompanionError("Cypress application checksum is invalid")
                self.position += 1
                self.finished = self.position == len(self.steps)
        except CompanionError:
            self.failed = True
            raise

    def exit_request(self) -> bytes:
        """Write-only exit packet, available only after application verification.

        Stock sends this through the write callback and closes the transport;
        it does not wait for a Cypress protocol response. Runtime recovery and
        a subsequent application handshake remain the native driver's job.
        """
        if self.failed or not self.finished:
            raise CompanionError("cannot exit before successful application verification")
        return cypress_packet(0x3B)


def crc16_ccitt(data: bytes, initial: int = 0) -> int:
    """Stock BLE transfer CRC: polynomial 0x1021, unreflected, no final XOR."""
    if not 0 <= initial <= 0xFFFF:
        raise CompanionError("CRC initial value must fit 16 bits")
    return binascii.crc_hqx(data, initial)        # the same CRC (CRC-CCITT, given initial value), in C


@dataclass(frozen=True)
class NordicApplication:
    data: bytes
    initial_stack: int
    reset_vector: int
    checksum: int
    sha256: str

    @classmethod
    def parse(cls, data: bytes) -> "NordicApplication":
        if not 8 <= len(data) <= 0x70000 or len(data) % 4:
            raise CompanionError("BLE application length is invalid")
        stack, reset = struct.unpack_from("<II", data)
        # These are vector-table sanity checks, not destination/chip verification.
        if stack & 7 or not 0x20000000 < stack <= 0x20040000:
            raise CompanionError("BLE image does not have a plausible Cortex-M stack")
        # The separately shipped Nordic bootloaders reset below 0x10000; they
        # are not application payloads and must not enter this programming path.
        if not reset & 1 or reset >= 0x100000 or reset < 0x10000:
            raise CompanionError("BLE image does not have an application Thumb reset vector")
        return cls(data, stack, reset, crc16_ccitt(data), sha256(data).hexdigest())

    def transfer_header(self, version: Tuple[int, int, int, int], device_id: int) -> bytes:
        """Encode the stock TE 64-byte header sent through bridge command 0x0d."""
        _validate_ble_metadata(version, device_id)
        # 1.2.1 Nordic bootloader: CRC-16 at bytes 9-10, version at 11-18.
        header = struct.pack(">IBIH4HII", 0xBEEFCAFE, 3, len(self.data), self.checksum,
                             *version, 0x1F000, device_id)
        return header.ljust(64, b"\0")

    def data_packets(self) -> Tuple[bytes, ...]:
        """Bridge request bodies; the bridge framing/ACK state is not implemented."""
        return tuple(b"\x03" + self.data[offset:offset + 0xF8]
                     for offset in range(0, len(self.data), 0xF8))


def _validate_ble_metadata(version: Tuple[int, int, int, int], device_id: int) -> None:
    if len(version) != 4 or any(not isinstance(x, int) or not 0 <= x <= 0xFFFF for x in version):
        raise CompanionError("BLE version needs four unsigned 16-bit fields")
    if not isinstance(device_id, int) or not 0 <= device_id <= 0xFFFFFFFF:
        raise CompanionError("BLE device identity must fit 32 bits")


def ble_start_packet(version: Tuple[int, int, int, int], image_kind: int, device_id: int) -> bytes:
    _validate_ble_metadata(version, device_id)
    if not 0 <= image_kind <= 255:
        raise CompanionError("BLE image kind must fit one byte")
    return b"\x02" + struct.pack(">4HBI", *version, image_kind, device_id)


# Bluetooth update session. Phase order and request tuples match
# native/ble_session.hpp exactly (the tests run both in lockstep).
BLE_PHASES = ("uninitialized", "query", "enter", "settle", "probe", "greeting", "answer",
              "start", "header", "data", "finish", "exit", "regreeting", "release", "verify",
              "complete", "aborting", "aborted", "failed")
BLE_WRITE, BLE_EXCHANGE, BLE_LISTEN, BLE_DELAY = range(4)
_BLE_ACCEPTS = {"query", "enter", "probe", "greeting", "start", "header", "data", "finish",
                "regreeting", "verify"}


def ble_metadata_field(payload: bytes, key: str) -> bytes:
    """Strict stock `key:value;...` parse; a missing or repeated key is an error."""
    if not 0 < len(payload) < 249:
        raise CompanionError("BLE metadata length is invalid")
    if b"\0" in payload:
        raise CompanionError("BLE metadata contains NUL")
    found = None
    for entry in (payload[:-1] if payload.endswith(b";") else payload).split(b";"):
        name, colon, value = entry.partition(b":")
        if not colon or not name:
            raise CompanionError("BLE metadata entry is malformed")
        if name == key.encode():
            if found is not None:
                raise CompanionError("BLE metadata key repeated")
            found = value
    if found is None:
        raise CompanionError("BLE metadata has no " + key)
    return found


def ble_parse_version(text: bytes) -> Tuple[int, int, int, int]:
    match = re.fullmatch(rb"(\d+)\.(\d+)\.(\d+)\+(\d+)", text)
    if match is None or any(int(x) > 0xFFFF for x in match.groups()):
        raise CompanionError("BLE version text is invalid")
    return tuple(int(x) for x in match.groups())


def ble_platform_word(hardware_revision: int) -> int:
    """Stock sends it in start and header; the chip ignores it, so it is no guard."""
    if not 0 <= hardware_revision < 1000:
        raise CompanionError("BLE hardware revision out of range")
    return (0x21 << 14) | hardware_revision


class BleSession:
    """Nordic update session; see native/ble_session.hpp for the contract.

    `known` maps the SHA-256 hex digest of each admitted official ble.bin to its
    version; nothing is sent unless the image matches one exactly. request()
    returns (action, command, payload, timeout_ms, delay_us). A bad reply stops
    in "failed" and raises; misuse raises without changing the phase.
    """

    def __init__(self, data: bytes, known, hardware_revision: int):
        self.phase = "failed"
        self.offset = 0
        self.probes = 0
        self.in_session = False
        self.application_valid = False
        if not 8 <= len(data) <= 0x70000 or len(data) % 4 or not known:
            raise CompanionError("BLE image or known list is invalid")
        version = dict(known).get(sha256(data).hexdigest())
        if version is None:
            raise CompanionError("BLE image is not a known official image")
        _validate_ble_metadata(tuple(version), 0)
        self.platform = ble_platform_word(hardware_revision)
        self.data = bytes(data)
        self.version = tuple(version)
        self.crc = crc16_ccitt(self.data)
        self.phase = "query"

    def _stop(self, reason: str):
        self.phase = "failed"
        raise CompanionError(reason)

    def request(self):
        p = self.phase
        if p in ("query", "probe", "verify"):
            return (BLE_EXCHANGE, 9, b"", 100, 0)
        if p == "enter":
            return (BLE_EXCHANGE, 0x0D, b"\xee", 100, 0)
        if p == "settle":
            return (BLE_DELAY, 0, b"", 0, 250000)
        if p in ("greeting", "regreeting"):
            return (BLE_LISTEN, 5, b"", 500 if p == "greeting" else 2500, 0)
        if p in ("answer", "aborting"):
            return (BLE_WRITE, 0x0D, b"\xee", 0, 0)
        if p in ("exit", "release") and self.application_valid:
            return (BLE_WRITE, 0x0D, b"\x18", 0, 0)
        if p == "start":
            return (BLE_EXCHANGE, 0x0D, ble_start_packet(self.version, 0xB0, self.platform), 1500, 0)
        if p == "header":
            header = NordicApplication(self.data, 0, 0, self.crc, "").transfer_header(
                self.version, self.platform)
            return (BLE_EXCHANGE, 0x0D, b"\x03" + header, 1000, 0)
        if p == "data":
            return (BLE_EXCHANGE, 0x0D, b"\x03" + self.data[self.offset:self.offset + 248], 1000, 0)
        if p == "finish":
            return (BLE_EXCHANGE, 0x0D, b"\x05", 1000, 0)
        raise CompanionError("BLE session has no request in phase " + p)

    def complete(self, success: bool) -> None:
        nxt = {"settle": "probe", "answer": "start", "exit": "regreeting",
               "release": "verify", "aborting": "aborted"}.get(self.phase)
        if nxt is None:
            raise CompanionError("BLE session has no write or delay in phase " + self.phase)
        if self.phase == "answer":
            self.in_session = True
        if not success:
            self._stop("BLE write failed")
        if self.phase in ("exit", "aborting"):
            self.in_session = False
        if self.phase == "release":
            self.probes = 0
        self.phase = nxt

    def accept(self, command: int, payload: bytes) -> None:
        p = self.phase
        if p not in _BLE_ACCEPTS:
            raise CompanionError("BLE session accepts no reply in phase " + p)
        if not 0 <= command <= 255 or not 0 < len(payload) <= 249:
            self._stop("BLE reply framing")
        if p in ("greeting", "regreeting"):
            if command != 5 or payload != b"\xbb":
                self._stop("expected the bootloader greeting [bb]")
            self.phase = "answer" if p == "greeting" else "release"
            return
        if p in ("query", "probe", "verify"):
            if command != 0x0A:
                self._stop("expected a metadata reply")
            try:
                mode = ble_metadata_field(payload, "mode")
            except CompanionError:
                self.phase = "failed"
                raise
            if mode not in (b"bootloader", b"normal"):
                self._stop("unknown BLE mode")
            if p == "query":
                self.phase = "greeting" if mode == b"bootloader" else "enter"
                return
            if mode == (b"bootloader" if p == "probe" else b"normal"):
                if p == "probe":
                    self.phase = "greeting"
                    return
                try:
                    running = ble_parse_version(ble_metadata_field(payload, "sw_version"))
                except CompanionError:
                    self.phase = "failed"
                    raise
                if running != self.version:
                    self._stop("running BLE version differs")
                self.phase = "complete"
                return
            self.probes += 1
            if self.probes == 20:
                self._stop("BLE chip never reached the expected mode")
            return
        if command != 0x0E or payload[0] != 0x54:
            self._stop("BLE chip replied error or unexpected 0x%02x" % payload[0])
        if p == "enter":
            self.phase = "settle"
        elif p == "start":
            self.phase = "header"
        elif p == "header":
            self.phase = "data"
        elif p == "data":
            self.offset += min(len(self.data) - self.offset, 248)
            if self.offset == len(self.data):
                self.phase = "finish"
        else:
            self.application_valid = True
            self.phase = "exit"

    def transport_failed(self) -> None:
        if self.phase in ("uninitialized", "complete", "aborted", "failed"):
            raise CompanionError("BLE session has stopped")
        if self.phase == "verify":
            self.probes += 1
            if self.probes < 20:
                return
        self._stop("BLE transport failed")

    def abort(self) -> None:
        if not self.in_session or self.phase in ("aborting", "aborted"):
            raise CompanionError("no BLE session to abort")
        self.phase = "aborting"
