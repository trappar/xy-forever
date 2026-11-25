"""Streamed, replace-safe file writes over XY Forever's serial protocol (1.18: file.write_begin, file.write_data,
file.write_end; #4). The bytes go straight to a temporary file on the device; the device checks them, reads the file
back and renames it onto the target in one step, so a cut cable or a power cut leaves the old file or the new one.
No size limit from the staging buffer. Used by xyf-host put and the tests."""

import hashlib
import struct
import zlib

from xy_forever import usb_proto as P

# put/rm/mkdir/mv/cp write only here (the device checks again: target/file_engine.hpp; xyf_host.py, clients/ts inWriteArea).
AREA_TEXT = 'under /yaffs2/, never bootloader.dev or upgrade/'
WRITE_ROOTS = ('/yaffs2/',)  # since 1.13: all of /yaffs2 but the two below


def in_write_area(path):
    """The device's write area (since 1.13): below /yaffs2/, no empty, '.' or '..' part, never
    /yaffs2/bootloader.dev nor /yaffs2/upgrade or below it (ASCII case ignored). The device checks again."""
    root = next((r for r in WRITE_ROOTS if path.startswith(r)), None)
    rest = path[len(root) :].split('/') if root else []
    if not rest or not all(p and p not in ('.', '..') for p in rest):
        return False
    first = rest[0].lower()
    return first != 'upgrade' and not (len(rest) == 1 and first == 'bootloader.dev')


def timeout_of(kind):
    return P.ids.COMMANDS[kind]['timeout_ms'] / 1000.0


def write_stream(dev, path, data, progress=None):
    """(status, written, readback_crc, error) of one streamed write; progress(done, total) after each piece."""
    crc = zlib.crc32(data) & 0xFFFFFFFF
    code, _ = dev.call(
        P.ids.WRITE_BEGIN,
        path.encode('utf-8') + b'\0' + struct.pack('<II', len(data), crc) + hashlib.sha256(data).digest(),
        timeout=timeout_of(P.ids.WRITE_BEGIN),
    )
    if code:
        return code, 0, 0, 0
    for offset in range(0, len(data), P.ids.PUT_CHUNK):
        piece = data[offset : offset + P.ids.PUT_CHUNK]
        code, _ = dev.call(P.ids.WRITE_DATA, struct.pack('<I', offset) + piece, timeout=timeout_of(P.ids.WRITE_DATA))
        if code:
            return code, offset, 0, 0
        if progress:
            progress(offset + len(piece), len(data))
    code, body = dev.call(P.ids.WRITE_END, timeout=60.0 + len(data) / 20000.0)
    written, back, error = struct.unpack_from('<III', body) if len(body) >= 12 else (0, 0, 0)
    return code, written, back, error


def streams(dev):
    """The device answers file.write_begin (1.18; a device without a hello list is taken to)."""
    info = getattr(dev, 'hello_info', None)
    return info is None or info.answers(P.ids.WRITE_BEGIN)


def hashes(dev):
    """The device hashes a stored file itself (file.hash, 1.32): a check needs no read-back over USB."""
    info = getattr(dev, 'hello_info', None)
    return info is not None and not info.pre_v1 and info.answers(P.ids.HASH_FILE)


def stored_sha256(dev, path, size=0):
    """(status, SHA-256) of the file stored at path, hashed on the device (file.hash, 1.32); size: the bytes expected
    (the time the host waits)."""
    code, body = dev.call(P.ids.HASH_FILE, path.encode('utf-8') + b'\0', timeout=60.0 + size / 20000.0)
    return code, body[4:36]


def holds(dev, path, data, read):
    """(status, whether the file stored at path holds exactly data): compared by SHA-256, hashed on the device from 1.32
    (stored_sha256), before it read back whole over USB with read(path) -> (status, bytes)."""
    want = hashlib.sha256(data).digest()
    if hashes(dev):
        code, sha = stored_sha256(dev, path, len(data))
    else:
        code, back = read(path)
        sha = hashlib.sha256(back).digest()
    return code, not code and sha == want


def write_staged(dev, path, data, progress=None):
    """Before 1.18 (the throughput table's "before" row): stage, then file.write_staged (in place, staging-sized)."""
    code = dev.stage(data, progress)
    if code:
        return code, 0, 0, 0
    code, written, back = dev.file_write(path, hashlib.sha256(data).digest(), timeout=60.0 + len(data) / 20000.0)
    return code, written, back, 0


def put(dev, data, remote, progress=None, say=print):
    """Write data to remote (streamed, then renamed onto it; staged on a device before 1.18); 0 only when written and
    read back exactly."""
    code, written, back, error = (write_stream if streams(dev) else write_staged)(dev, remote, data, progress)
    want = zlib.crc32(data) & 0xFFFFFFFF
    say(
        'put-file %s: %s, written %d/%d, read-back CRC %08x (expected %08x)%s'
        % (remote, P.status_name(code), written, len(data), back, want, ', error %d' % error if error else '')
    )
    if code == 0 and (written != len(data) or back != want):
        say('NOT verified')
        return 1
    return code
