"""Streamed, replace-safe file writes over XY Forever's serial protocol (1.18: file.write_begin, file.write_data,
file.write_end; #4). The bytes go straight to a temporary file on the device; the device checks them, reads the file
back and renames it onto the target in one step, so a cut cable or a power cut leaves the old file or the new one.
No size limit from the staging buffer. Used by xyf_host.py put, scripts/throughput.py and the tests."""
import hashlib
import struct
import zlib

from xy_forever import usb_proto as P


def timeout_of(kind):
    return P.ids.COMMANDS[kind]['timeout_ms'] / 1000.0


def write_stream(dev, path, data, progress=None):
    """(status, written, readback_crc, error) of one streamed write; progress(done, total) after each piece."""
    crc = zlib.crc32(data) & 0xffffffff
    code, _ = dev.call(P.ids.WRITE_BEGIN, path.encode('utf-8') + b'\0' + struct.pack('<II', len(data), crc) +
                       hashlib.sha256(data).digest(), timeout=timeout_of(P.ids.WRITE_BEGIN))
    if code:
        return code, 0, 0, 0
    for offset in range(0, len(data), P.ids.PUT_CHUNK):
        piece = data[offset:offset + P.ids.PUT_CHUNK]
        code, _ = dev.call(P.ids.WRITE_DATA, struct.pack('<I', offset) + piece,
                           timeout=timeout_of(P.ids.WRITE_DATA))
        if code:
            return code, offset, 0, 0
        if progress:
            progress(offset + len(piece), len(data))
    code, body = dev.call(P.ids.WRITE_END, timeout=60.0 + len(data) / 20000.0)
    written, back, error = struct.unpack_from('<III', body) if len(body) >= 12 else (0, 0, 0)
    return code, written, back, error


def put(dev, data, remote, progress=None, say=print):
    """Write data to remote (streamed, then renamed onto it); 0 only when written and read back exactly."""
    code, written, back, error = write_stream(dev, remote, data, progress)
    want = zlib.crc32(data) & 0xffffffff
    say('put-file %s: %s, written %d/%d, read-back CRC %08x (expected %08x)%s' %
        (remote, P.status_name(code), written, len(data), back, want, ', error %d' % error if error else ''))
    if code == 0 and (written != len(data) or back != want):
        say('NOT verified')
        return 1
    return code
