"""The conformance runner's device guard (xy_forever/conformance.py): which frames a real device may get.
Split out of conformance.py (source budget). Stdlib only."""

import hashlib
import struct

REPLY_BIT = 0x80
UNSAFE_TOKEN = b'\xff' * 32  # a confirmation token no staging's SHA-256 matches


def path_in(path, area):
    if not path.startswith(area):
        return False
    parts = path[len(area) :].split('/')
    return all(p and p not in ('.', '..') for p in parts)


def device_guard(spec, kind, payload, test_area):
    """None when a real device may get this request, else why not. Allowed: reads (file.hash among them), the screenshot,
    staging, storage.space version 1, file.write_staged, file.delete, file.rmdir, file.mkdir, file.rename, file.copy (its target) and
    file.write_begin (1.18; with file.write_data and file.write_end) in the test area (or with a token
    no staging or path matches), file.list, job.status, a mods.apply or job.cancel the device refuses, every
    restart into the menu, and the requests the device refuses without doing anything (unknown
    types, menu.continue modes it answers link, service.hold values it answers bad_request).
    Never: boot.install, boot.uninstall, library.set_default, test-only commands, the OS, a reserved
    (developer or retired) type with a payload (builds before 1.14 still act on 0x33 diag.guard and 0x60
    diag.hang; an empty one they refuse), or a request above put_chunk data bytes."""
    command = spec.by_type.get(kind)
    if kind >= REPLY_BIT:
        return 'type 0x%02x is not a request' % kind
    if len(payload) > spec.constants['put_chunk'] + 4:
        return 'a %d-byte request (Test 034: frames above put_chunk overflowed the device)' % len(payload)
    if command is None:
        for r in spec.raw['reserved']['types']:
            if payload and r['kind'] in ('dev', 'retired') and int(r['first'], 16) <= kind <= int(r['last'], 16):
                return 'type 0x%02x is a reserved type with a payload (an older build may act on it)' % kind
        return None
    name = command['name']
    value = struct.unpack('<I', payload)[0] if len(payload) == 4 else None
    if name in (
        'hello',
        'diag.status',
        'file.read',
        'file.hash',
        'file.list',
        'screen.read',
        'stage.begin',
        'stage.data',
        'stage.end',
        'job.status',
    ):
        return None
    if name == 'storage.space' and value == 1:
        return None  # read-only startup readiness probe, version 1
    if name == 'mods.apply':  # 1.10: only requests refused before anything
        if (
            len(payload) < 33
            or hashlib.sha256(payload[:-32]).digest() != payload[-32:]
            or payload.startswith(b'xyf-conformance-')
        ):
            return None  # bad_request, sha, or a base no device holds
        return 'mods.apply is sent to a real device only when refused (sha, or a base named xyf-conformance-*)'
    if name == 'job.cancel':
        if value is None or value == 0xFFFFFFFF:
            return None  # bad_request, or a job id no job has
        return 'job.cancel is sent to a real device only for job 4294967295 (no such job)'
    if name in ('file.delete', 'file.rmdir', 'file.mkdir'):
        end = payload.find(b'\0')
        if end < 0 or len(payload) != end + 1 + 32:
            return None  # bad_request: the length does not match
        path = payload[:end].decode('utf-8', 'replace')
        if path_in(path, test_area) or payload[end + 1 :] == UNSAFE_TOKEN:
            return None
        return '%s outside the test area %s needs a token no path matches' % (name, test_area)
    if name == 'file.rename':
        first = payload.find(b'\0')
        second = payload.find(b'\0', first + 1) if first >= 0 else -1
        if second < 0 or len(payload) != second + 1 + 32:
            return None  # bad_request: the length does not match
        paths = [payload[:first].decode('utf-8', 'replace'), payload[first + 1 : second].decode('utf-8', 'replace')]
        if all(path_in(p, test_area) for p in paths) or payload[second + 1 :] == UNSAFE_TOKEN:
            return None
        return 'file.rename outside the test area %s needs a token no pair matches' % test_area
    if name == 'file.copy':  # 1.9: the source is only read
        first = payload.find(b'\0')
        second = payload.find(b'\0', first + 1) if first >= 0 else -1
        if second < 0 or len(payload) != second + 1 + 32:
            return None  # bad_request: the length does not match
        if (
            path_in(payload[first + 1 : second].decode('utf-8', 'replace'), test_area)
            or payload[second + 1 :] == UNSAFE_TOKEN
        ):
            return None
        return 'file.copy outside the test area %s needs a token no pair matches' % test_area
    if name == 'file.write_staged':
        end = payload.find(b'\0')
        if end < 0 or len(payload) != end + 1 + 32:
            return None  # bad_request: the length does not match
        path = payload[:end].decode('utf-8', 'replace')
        if path_in(path, test_area) or payload[end + 1 :] == UNSAFE_TOKEN:
            return None
        return 'file.write_staged outside the test area %s needs a token no staging matches' % test_area
    if name == 'file.write_begin':  # 1.18: data and end only reach what it names
        end = payload.find(b'\0')
        if end < 0 or len(payload) != end + 1 + 40:
            return None  # bad_request: the length does not match
        if path_in(payload[:end].decode('utf-8', 'replace'), test_area):
            return None
        return 'file.write_begin outside the test area %s' % test_area
    if name in ('file.write_data', 'file.write_end'):
        return None
    if name == 'device.reboot':
        return 'device.reboot to the OS' if value == 1 else None
    if name == 'menu.continue':
        return (
            None
            if (len(payload) not in (0, 4) or (value is not None and value >= 2))
            else 'menu.continue leaves the menu'
        )
    if name == 'service.hold':
        return None if value is None or value > 1 else 'service.hold changes every restart until power-off'
    return '%s is never sent to a real device' % name
