"""Standalone bounded ELF32 and BF70x loader inspection for build verification."""

import hashlib
import struct
from dataclasses import dataclass

from elftools.elf.enums import ENUM_SH_TYPE_BASE, ENUM_ST_INFO_TYPE
from elftools.elf.structs import ELFStructs


def require(condition, message):
    if not condition:
        raise ValueError(message)


def header(flags, target, count, argument):
    require(
        0 <= flags <= 65535 and all(0 <= v <= 0xFFFFFFFF for v in (target, count, argument)),
        'Loader header field overflow',
    )
    raw = bytearray(struct.pack('<4I', 0xAD000000 | flags, target, count, argument))
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
        require(code >> 24 == 0xAD and checksum == 0, 'Invalid loader header')
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


def elf_records(data):
    """pyelftools' ELF32 little-endian record layouts and the parsed file header (data: 52 bytes or more). Records
    are parsed one by one, without pyelftools' own section checks: the readers' checks come first and name the
    problem."""
    structs = ELFStructs(little_endian=True, elfclass=32)
    structs.create_basic_structs()
    header = structs.Elf_Ehdr.parse(data[:52])
    structs.create_advanced_structs(header['e_type'], header['e_machine'], header['e_ident']['EI_OSABI'])
    return structs, header


def elf32(data, relocations=False):
    """Sections and named symbols of a linked ELF32. relocations=True (a `ld -q` link) also returns its RELA
    entries: {relocated section: [dict(offset, type, target)]}, offset an address, target symbol + addend."""
    require(len(data) >= 52 and data[:7] == b'\x7fELF\x01\x01\x01', 'Expected little-endian ELF32')
    structs, elf = elf_records(data)
    require(elf['e_machine'] in ('EM_BLACKFIN', 106), 'Expected Blackfin machine type')
    offset, count, names_index = elf['e_shoff'], elf['e_shnum'], elf['e_shstrndx']
    require(
        elf['e_shentsize'] == 40 and 0 < count < 4096 and names_index < count and offset + 40 * count <= len(data),
        'Invalid ELF table',
    )
    headers = [structs.Elf_Shdr.parse(data[offset + 40 * i : offset + 40 * i + 40]) for i in range(count)]

    def contents(h):
        if h['sh_type'] == 'SHT_NOBITS':
            return b''
        require(h['sh_offset'] <= len(data) and h['sh_size'] <= len(data) - h['sh_offset'], 'Truncated ELF section')
        return data[h['sh_offset'] : h['sh_offset'] + h['sh_size']]

    def string(table, index):
        require(0 <= index < len(table), 'Invalid ELF string offset')
        end = table.find(b'\0', index)
        require(end >= index, 'Unterminated ELF string')
        return table[index:end].decode('ascii')

    names = contents(headers[names_index])
    sections = {}
    symbols = {}
    values = {}  # symbol table index -> every entry's value, by symbol index
    for index, h in enumerate(headers):
        name = string(names, h['sh_name'])
        kind = number(h['sh_type'], ENUM_SH_TYPE_BASE)
        require(name not in sections, 'Duplicate ELF section')
        require(not (kind in (4, 9) and h['sh_size']) or relocations and kind == 4, 'Unresolved ELF relocations')
        body = contents(h)
        sections[name] = dict(type=kind, flags=h['sh_flags'], address=h['sh_addr'], size=h['sh_size'], data=body)
        if kind != 2:
            continue
        require(h['sh_link'] < count and h['sh_entsize'] == 16 and h['sh_size'] % 16 == 0, 'Invalid ELF symbols')
        strings = contents(headers[h['sh_link']])
        values[index] = []
        for at in range(0, len(body), 16):
            e = structs.Elf_Sym.parse(body[at : at + 16])
            values[index].append(e['st_value'])
            symbol = string(strings, e['st_name'])
            if symbol:
                require(e['st_shndx'] != 'SHN_UNDEF', 'Undefined ELF symbol: ' + symbol)
                symbols[symbol] = dict(
                    value=e['st_value'], size=e['st_size'], type=number(e['st_info']['type'], ENUM_ST_INFO_TYPE)
                )
    if not relocations:
        return sections, symbols
    relocated = {}
    for h in headers:
        if h['sh_type'] != 'SHT_RELA' or not h['sh_size']:
            continue
        require(
            h['sh_link'] in values and h['sh_info'] < count and h['sh_entsize'] == 12 and h['sh_size'] % 12 == 0,
            'Invalid ELF relocations',
        )
        table, body = values[h['sh_link']], contents(h)
        entries = relocated.setdefault(string(names, headers[h['sh_info']]['sh_name']), [])
        for at in range(0, len(body), 12):
            r = structs.Elf_Rela.parse(body[at : at + 12])
            require(r['r_info_sym'] < len(table), 'Invalid ELF relocation symbol')
            entries.append(
                dict(
                    offset=r['r_offset'],
                    type=r['r_info_type'],
                    target=(table[r['r_info_sym']] + r['r_addend']) & 0xFFFFFFFF,
                )
            )
    return sections, symbols, relocated


def number(value, names):
    """pyelftools names the enum values it knows: the number back."""
    return names[value] if isinstance(value, str) else value
