"""Developer A admission rehearsal. Uses only RAM staging; never invokes an install."""
import hashlib
import struct

from xy_forever import transport, usb_proto_ids as P


def call(dev, operation, extra=b''):
    code, body = dev.call(P.A_REHEARSAL, struct.pack('<II', 1, operation) + extra)
    if code:
        return code, None
    if len(body) < 56 or struct.unpack_from('<I', body)[0] != 1:
        raise ValueError('unsupported A rehearsal reply version or truncated reply')
    version, phase, done, total, crc, result = struct.unpack_from('<6I', body)
    return code, dict(version=version, phase=phase, done=done, total=total, crc32=crc,
                      result=result, sha256=body[24:56].hex())


def run(dev, data, uninstall=False, deadline_ms=120000, progress=None):
    code = dev.stage(data, progress)
    if code:
        return code, None
    digest = hashlib.sha256(data).digest()
    code, state = call(dev, 0, struct.pack('<II', int(uninstall), deadline_ms) + digest)
    if code:
        return code, state
    try:
        while state['phase']:
            code, state = call(dev, 1)
            if code:
                return code, state
            transport.pause(dev.t, .02)  # status polling cadence (the simulator: its clock); never retries a failed request
    except KeyboardInterrupt:
        # Cancel only an intact protocol session. Transport failures propagate without recovery.
        code, state = call(dev, 2)
        while not code and state['phase']:
            code, state = call(dev, 1)
        return code or state['result'], state
    if not state['result'] and state['sha256'] != digest.hex():
        raise ValueError('A admission digest differs from the input')
    return state['result'], state
