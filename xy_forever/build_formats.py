"""Standalone bounded ELF32 and BF70x loader inspection for build verification."""

from dataclasses import dataclass
import hashlib
import struct


def require(condition, message):
    if not condition:
        raise ValueError(message)


def header(flags, target, count, argument):
    require(
        0 <= flags <= 65535 and all(0 <= v <= 0xffffffff for v in (target, count, argument)),
        'Loader header field overflow',
    )
    raw = bytearray(struct.pack('<4I', 0xad000000 | flags, target, count, argument))
    checksum = 0
    for byte in raw:
        checksum ^= byte
    raw[2] = checksum
    return bytes(raw)


@dataclass(frozen=True)
class Record:
    index: int
    offset: int
    flags: int
    address: int
    length: int
    argument: int

    @property
    def fill(self):
        return bool(self.flags & 0x100)

    @property
    def ignored(self):
        return bool(self.flags & 0x1000)


def parse_stream(data):
    require(0 < len(data) <= 16 * 1024 * 1024, 'Loader image size outside supported bounds')
    offset = 0
    records = []
    while offset < len(data):
        require(len(records) < 10000 and offset + 16 <= len(data), 'Truncated/excessive loader records')
        raw = data[offset : offset + 16]
        code, address, length, argument = struct.unpack('<4I', raw)
        checksum = 0
        for byte in raw:
            checksum ^= byte
        require(code >> 24 == 0xad and checksum == 0, 'Invalid loader header')
        require(address + length <= 0x100000000, 'Loader address overflow')
        record = Record(len(records) + 1, offset, code & 65535, address, length, argument)
        stored = 0 if record.fill else length
        require(stored <= len(data) - offset - 16, 'Truncated loader payload')
        records.append(record)
        offset += 16 + stored
        if record.flags & 0x8000:
            require(offset == len(data), 'Trailing data after FINAL')
            return records
    raise ValueError('Missing FINAL record')


class Firmware:
    """Final-memory oracle; mapping does not execute INIT or ROM callbacks.

    Every final interval is selected from the complete boundary set, choosing
    its last covering record. This differs from the native sequential decoder.
    """

    def __init__(self, data):
        self.data = bytes(data)
        self.records = parse_stream(data)
        writes = [r for r in self.records if r.length and not r.ignored]
        require(not any(r.flags & 0x400 for r in writes), 'Callback/compressed streams cannot be flattened')
        boundaries = sorted({edge for r in writes for edge in (r.address, r.address + r.length)})
        self.segments = []
        for lo, hi in zip(boundaries, boundaries[1:]):
            record = next((r for r in reversed(writes) if r.address <= lo and hi <= r.address + r.length), None)
            if record is None:
                continue
            if self.segments and self.segments[-1][1] == lo and self.segments[-1][2] is record:
                old = self.segments[-1]
                self.segments[-1] = (old[0], hi, record)
            else:
                self.segments.append((lo, hi, record))
        require(sum(hi - lo for lo, hi, _ in self.segments) <= 256 * 1024 * 1024, 'Mapped image exceeds 256 MiB')

    def bytes_for(self, record, address, count):
        delta = address - record.address
        if record.fill:
            begin = delta & 3
            pattern = struct.pack('<I', record.argument)
            return (pattern * ((begin + count + 3) // 4))[begin : begin + count]
        at = record.offset + 16 + delta
        return self.data[at : at + count]

    def read(self, address, count):
        require(0 <= address <= address + count <= 0x100000000, 'Invalid memory interval')
        end = address + count
        result = bytearray()
        for lo, hi, record in self.segments:
            if hi <= address or lo >= end:
                continue
            require(lo <= address, 'Unmapped memory interval')
            amount = min(hi, end) - address
            result.extend(self.bytes_for(record, address, amount))
            address += amount
            if address == end:
                break
        require(address == end, 'Unmapped memory interval')
        return bytes(result)

    def memory_manifest(self):
        rows = []
        for lo, hi, record in self.segments:
            digest = hashlib.sha256()
            for at in range(lo, hi, 65536):
                digest.update(self.bytes_for(record, at, min(65536, hi - at)))
            rows.append(
                dict(
                    address=hex(lo),
                    length=hi - lo,
                    record=record.index,
                    fill=record.fill,
                    file_offset=None if record.fill else record.offset + 16 + lo - record.address,
                    sha256=digest.hexdigest(),
                )
            )
        return rows


def elf32(data, relocations=False):
    """Sections and named symbols of a linked ELF32. relocations=True (a `ld -q` link) also returns its RELA
    entries: {relocated section: [dict(offset, type, target)]}, offset an address, target symbol + addend."""
    require(len(data) >= 52 and data[:7] == b'\x7fELF\x01\x01\x01', 'Expected little-endian ELF32')
    require(struct.unpack_from('<H', data, 18)[0] == 106, 'Expected Blackfin machine type')
    offset = struct.unpack_from('<I', data, 32)[0]
    stride, count, names_index = struct.unpack_from('<3H', data, 46)
    require(
        stride == 40 and 0 < count < 4096 and names_index < count and offset + 40 * count <= len(data),
        'Invalid ELF table',
    )
    headers = [struct.unpack_from('<10I', data, offset + 40 * i) for i in range(count)]

    def contents(h):
        if h[1] == 8:
            return b''
        require(h[4] <= len(data) and h[5] <= len(data) - h[4], 'Truncated ELF section')
        return data[h[4] : h[4] + h[5]]

    def string(table, index):
        require(0 <= index < len(table), 'Invalid ELF string offset')
        end = table.find(b'\0', index)
        require(end >= index, 'Unterminated ELF string')
        return table[index:end].decode('ascii')

    names = contents(headers[names_index])
    sections = {}
    symbols = {}
    values = {}                                 # symbol table index -> every entry's value, by symbol index
    for index, h in enumerate(headers):
        name = string(names, h[0])
        require(name not in sections, 'Duplicate ELF section')
        require(not (h[1] in (4, 9) and h[5]) or relocations and h[1] == 4, 'Unresolved ELF relocations')
        body = contents(h)
        sections[name] = dict(type=h[1], flags=h[2], address=h[3], size=h[5], data=body)
        if h[1] != 2:
            continue
        require(h[6] < count and h[9] == 16 and h[5] % 16 == 0, 'Invalid ELF symbols')
        strings = contents(headers[h[6]])
        values[index] = []
        for at in range(0, len(body), 16):
            n, value, size, info, other, section = struct.unpack_from('<IIIBBH', body, at)
            values[index].append(value)
            symbol = string(strings, n)
            if symbol:
                require(section != 0, 'Undefined ELF symbol: ' + symbol)
                symbols[symbol] = dict(value=value, size=size, type=info & 15)
    if not relocations:
        return sections, symbols
    relocated = {}
    for h in headers:
        if h[1] != 4 or not h[5]:
            continue
        require(h[6] in values and h[7] < count and h[9] == 12 and h[5] % 12 == 0, 'Invalid ELF relocations')
        table, body = values[h[6]], contents(h)
        entries = relocated.setdefault(string(names, headers[h[7]][0]), [])
        for at in range(0, len(body), 12):
            offset, info, addend = struct.unpack_from('<IIi', body, at)
            require(info >> 8 < len(table), 'Invalid ELF relocation symbol')
            entries.append(dict(offset=offset, type=info & 0xff, target=(table[info >> 8] + addend) & 0xffffffff))
    return sections, symbols, relocated
