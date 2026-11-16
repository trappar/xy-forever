"""Strict BF70x OS stream parsing; no input code or device operations run."""
from dataclasses import dataclass
import hashlib
import struct

MAX_IMAGE = 16 * 1024 * 1024
MAX_RECORDS = 10000
MAX_MAPPED = 256 * 1024 * 1024

@dataclass(frozen=True)
class Record:
    offset: int
    flags: int
    target: int
    count: int
    argument: int

    @property
    def fill(self):
        return self.flags == 0x104

def parse_ldr(data):
    if not 32 <= len(data) <= MAX_IMAGE:
        raise ValueError("LDR must be 32 bytes to 16 MiB")
    records, offset, mapped = [], 0, 0
    while offset < len(data):
        if len(records) >= MAX_RECORDS or offset + 16 > len(data):
            raise ValueError("Truncated or excessive LDR records")
        raw = data[offset:offset + 16]
        code, target, count, argument = struct.unpack("<4I", raw)
        checksum = 0
        for byte in raw:
            checksum ^= byte
        if code >> 24 != 0xAD or checksum:
            raise ValueError(f"Invalid header/checksum at {offset:#x}")
        flags = code & 0xFFFF
        record = Record(offset, flags, target, count, argument)
        if not records:
            if flags != 0x5004 or count or argument != len(data) - 16:
                raise ValueError("Expected length-bound initial IGNORE/FIRST record")
        elif flags not in (4, 0x104, 0x8004):
            raise ValueError(f"Unsupported LDR flags {flags:#x}")
        if target + count >= 0x100000000:
            raise ValueError("LDR address overflow")
        stored = 0 if record.fill else count
        end = offset + 16 + stored
        if end > len(data):
            raise ValueError("Truncated LDR data")
        if flags in (4, 0x104):
            mapped += count
            if mapped > MAX_MAPPED:
                raise ValueError("Expanded writes exceed 256 MiB")
        if flags == 0x8004:
            if count or argument or end != len(data) or target & 1:
                raise ValueError("Invalid FINAL entry record")
            for address in (target, target + 1):
                entry = next((r for r in reversed(records) if r.flags in (4, 0x104)
                              and r.target <= address < r.target + r.count), None)
                if entry is None or entry.fill:
                    raise ValueError("Entry point has no stored instruction halfword")
            return tuple(records + [record])
        records.append(record)
        offset = end
    raise ValueError("Missing FINAL record")

def inspect_ldr(data):
    records = parse_ldr(data)
    return dict(sha256=hashlib.sha256(data).hexdigest(), bytes=len(data),
                records=len(records), entry=hex(records[-1].target),
                expanded_write_bytes=sum(r.count for r in records[1:-1]),
                fill_records=sum(r.fill for r in records), hardware_tested=False,
                limitation="Structural validation; ROM and CPU handoff are not emulated")

def validate_memory(records, allowed, protected=()):
    """Half-open ranges; callers must include all aliases of live RAM."""
    for lo, hi in (*allowed, *protected):
        if not 0 <= lo < hi < 0x100000000:
            raise ValueError("Invalid memory policy range")
    for r in records[1:-1]:
        if not r.count:
            continue
        if not any(lo <= r.target and r.target + r.count <= hi for lo, hi in allowed):
            raise ValueError(f"Write outside allowed RAM at {r.target:#x}")
        if any(lo < r.target + r.count and r.target < hi for lo, hi in protected):
            raise ValueError(f"Write overlaps live loader/source/stack at {r.target:#x}")

def replay(data, allowed, protected, write):
    """Host replay: validate immutable stream before any callback writes."""
    data = bytes(data)
    records = parse_ldr(data)
    validate_memory(records, allowed, protected)
    for r in records[1:-1]:
        pattern = struct.pack("<I", r.argument)
        for delta in range(0, r.count, 4096):
            amount = min(4096, r.count - delta)
            chunk = ((pattern * ((amount + 3) // 4))[:amount] if r.fill else
                     data[r.offset + 16 + delta:r.offset + 16 + delta + amount])
            write(r.target + delta, chunk)
    return records[-1].target
