"""Prepare an XY Forever firmware-library entry (docs/STORAGE.md)
on the host: firmware/<id>/{os.ldr, manifest.json}. Offline only."""

import hashlib
import re
import zlib
from pathlib import Path

from . import package_records
from .ldr import MAX_IMAGE, inspect_ldr

ID = re.compile(r'^[a-z0-9_-]{1,31}$')
KINDS = ('official', 'open-xy', 'custom')


def info_text(name, version, kind, data):
    """Write legacy firmware metadata for compatibility fixtures."""
    return manifest_text(firmware_fields(name, version, kind, data))


def firmware_fields(name, version, kind, data):
    return dict(
        type='firmware',
        name=name,
        version=version or None,
        kind=kind,
        bytes=len(data),
        crc32='%08x' % (zlib.crc32(data) & 0xFFFFFFFF),
        sha256=hashlib.sha256(data).hexdigest(),
    )


def firmware_document(name, version, kind, data):
    return package_records.document(firmware_fields(name, version, kind, data))


def make_entry(ldr, out_root, entry_id, name, version='', kind='custom'):
    if not ID.fullmatch(entry_id):
        raise ValueError('id must be 1-31 of [a-z0-9_-]')
    if kind not in KINDS:
        raise ValueError('kind must be one of ' + ', '.join(KINDS))
    data = Path(ldr).read_bytes()
    if len(data) > MAX_IMAGE:
        raise ValueError('LDR exceeds 16 MiB')
    structure = inspect_ldr(data)
    folder = Path(out_root) / 'xy-forever' / 'firmware' / entry_id
    if folder.exists():
        raise ValueError('entry folder already exists: ' + str(folder))
    metadata = firmware_document(name, version, kind, data)
    folder.mkdir(parents=True)
    (folder / 'os.ldr').write_bytes(data)
    (folder / package_records.NATIVE).write_bytes(metadata)
    return dict(
        folder=str(folder),
        bytes=len(data),
        crc32=f'{zlib.crc32(data) & 0xFFFFFFFF:08x}',
        sha256=structure['sha256'],
        entry=structure['entry'],
        device_installation=False,
    )


# The package manifests (docs/storage/packages.md, docs/STORAGE.md): the host twin of
# target/xyf_manifest.hpp. parse_manifest and read_files_sha256 give the same answer as the
# device readers (tests/test_xyf_manifest.py compares them); the writers emit only what they accept.
MANIFEST_LIMITS = dict(text=4096, line=255, keys=32, key=31, value=127)
MANIFEST_KEYS = (
    'type',
    'name',
    'version',
    'kind',
    'bytes',
    'crc32',
    'sha256',
    'content',
    'needs_os',
    'generation',
    'profile',
    'machine',
    'source',
    'licence',
    'machines',
)
MANIFEST_STATUS = (
    'ok',
    'too_large',
    'empty',
    'no_newline',
    'line_too_long',
    'bad_byte',
    'syntax',
    'bad_key',
    'duplicate_key',
    'too_many_keys',
    'value_too_long',
    'empty_value',
    'bad_number',
    'bad_hex',
    'bad_value',
    'unknown_type',
    'refused_type',
    'key_not_for_type',
    'missing_key',
)
TYPES = ('firmware', 'mod-base', 'mod', 'companion')
MACHINES = ('synth', 'drum', 'sample', 'effect', 'player', 'extension')
_CAPACITY = dict(name=48, version=32, content=32, generation=32, profile=32, source=128, licence=64)
_FIRMWARE_KEYS = {
    'name',
    'version',
    'kind',
    'bytes',
    'crc32',
    'sha256',
    'content',
    'needs_os',
    'generation',
    'profile',
    'source',
    'licence',
    'machines',
}
_ALLOWED = {
    None: _FIRMWARE_KEYS,
    'firmware': _FIRMWARE_KEYS,
    'mod-base': {'name', 'version', 'profile', 'generation', 'needs_os', 'source', 'licence', 'machines'},
    'mod': {'name', 'version', 'machine', 'generation', 'profile', 'source', 'licence'},
}
_NEEDS = {
    None: set(),
    'firmware': {'name', 'bytes', 'crc32', 'sha256'},
    'mod-base': {'profile', 'generation', 'needs_os'},
    'mod': {'name', 'machine', 'generation'},
}
_HEX = re.compile(rb'[0-9a-f]+')
_TOKEN = re.compile(rb'[a-z0-9][a-z0-9._-]*')


class ManifestError(ValueError):
    def __init__(self, status, line=0, key=None, type_=None):
        super().__init__('%s (line %d%s)' % (status, line, ', key ' + key if key else ''))
        self.status, self.line, self.key, self.type = status, line, key, type_


def _value(key, v):
    """The stored value of one known key, or the status refusing it."""
    if key == 'type':
        return (v.decode(), None) if v.decode() in TYPES else (None, 'unknown_type')
    if key == 'kind':
        return (v.decode(), None) if v.decode() in KINDS else (None, 'bad_value')
    if key == 'machine':
        return (v.decode(), None) if v.decode() in MACHINES else (None, 'bad_value')
    if key in ('bytes', 'machines'):
        ok = (
            v.isdigit()
            and len(v) <= 10
            and (len(v) == 1 or v[:1] != b'0')
            and int(v) < (1 << 16 if key == 'machines' else 1 << 32)
        )
        return (int(v), None) if ok else (None, 'bad_number')
    if key in ('crc32', 'sha256', 'needs_os'):
        ok = _HEX.fullmatch(v) and len(v) == (8 if key == 'crc32' else 64)
        return (v.decode(), None) if ok else (None, 'bad_hex')
    if len(v) >= _CAPACITY[key]:
        return None, 'value_too_long'
    if key in ('generation', 'profile') and not _TOKEN.fullmatch(v):
        return None, 'bad_value'
    return v.decode(), None


def parse_manifest(data):
    """info.txt bytes -> {key: value} of the known keys present (bytes as int, hex as text)."""
    limits = MANIFEST_LIMITS
    if len(data) > limits['text']:
        raise ManifestError('too_large')
    if not data:
        raise ManifestError('empty')
    if not data.endswith(b'\n'):
        n = data.count(b'\n') + 1
        raise ManifestError('no_newline', n)
    fields, seen, lines, type_ = {}, [], {}, None
    raw = data[:-1].split(b'\n')
    for number, line in enumerate(raw, 1):

        def fail(status, key=None):
            raise ManifestError(status, number, key, type_)

        if line.endswith(b'\r'):
            line = line[:-1]
        if len(line) > limits['line']:
            fail('line_too_long')
        if any(c != 9 and not 0x20 <= c <= 0x7E for c in line):
            fail('bad_byte')
        line = line.lstrip(b' \t')
        if not line or line.startswith(b'#'):
            continue
        m = re.match(rb'[a-z0-9_]*', line)
        key, rest = m.group(), line[m.end() :]
        if not key or len(key) > limits['key']:
            fail('bad_key')
        after = rest.lstrip(b' \t')
        if not after.startswith(b'='):
            fail('syntax' if not after or rest[:1] in (b' ', b'\t') else 'bad_key')
        value = after[1:].strip(b' \t')
        if b'\t' in value:
            fail('bad_byte')
        name = key.decode()
        known = name if name in MANIFEST_KEYS else None
        if key in seen:
            fail('duplicate_key', known)
        if len(seen) == limits['keys']:
            fail('too_many_keys', known)
        seen.append(key)
        if known is None:
            if len(value) > limits['value']:
                fail('value_too_long')
            continue
        if not value:
            fail('empty_value', known)
        if len(value) > limits['value']:
            fail('value_too_long', known)
        stored, status = _value(known, value)
        if status:
            fail(status, known)
        if known == 'type':
            type_ = stored
        fields[known], lines[known] = stored, number
    if not seen:
        raise ManifestError('empty', 0, None, type_)
    if type_ == 'companion':
        raise ManifestError('refused_type', lines['type'], 'type', type_)
    extra = set(fields) - _ALLOWED[type_] - {'type'}
    if extra:
        key = min(extra, key=lines.get)
        raise ManifestError('key_not_for_type', lines[key], key, type_)
    needs = set(_NEEDS[type_])
    if type_ in (None, 'firmware') and ({'generation', 'needs_os'} & set(fields)):
        needs |= {'generation', 'needs_os'}
    missing = [k for k in MANIFEST_KEYS if k in needs - set(fields)]
    if missing:
        raise ManifestError('missing_key', 0, missing[0], type_)
    return fields


def manifest_text(fields, extra=()):
    """Canonical info.txt for the given keys (MANIFEST_KEYS order, LF); refused unless parse_manifest accepts it."""
    fields, unknown = dict(fields), []
    for key, value in extra:
        if not re.fullmatch(r'[a-z0-9_]{1,31}', key) or not str(value) or any(c in str(value) for c in '\r\n'):
            raise ManifestError('syntax')
        if key in fields:
            raise ManifestError('duplicate_key')
        if key in MANIFEST_KEYS:
            fields[key] = value
        else:
            unknown.append((key, value))
    lines = []
    for key in MANIFEST_KEYS:
        if key in fields and fields[key] is not None:
            value = fields[key]
            lines.append('%s=%s' % (key, '%d' % value if key == 'bytes' else value))
    lines += ['%s=%s' % (key, value) for key, value in unknown]
    text = ('\n'.join(lines) + '\n').encode() if lines else b''
    parse_manifest(text)
    return text.decode()


LIST_STATUS = (
    'ok',
    'empty',
    'no_newline',
    'line_too_long',
    'bad_hex',
    'syntax',
    'bad_path',
    'refused_path',
    'duplicate',
    'order',
    'too_many',
    'stopped',
)
LIST_LIMITS = dict(path=160, files=4096)


class ListError(ValueError):
    def __init__(self, status, line):
        super().__init__('files.sha256: %s (line %d)' % (status, line))
        self.status, self.line = status, line


def _list_path_ok(path):
    if not 0 < len(path) <= LIST_LIMITS['path']:
        return False
    if any(not 0x20 <= c <= 0x7E or c in b'\\:' for c in path):
        return False
    return all(part and part[-1:] not in (b'.', b' ') for part in path.split(b'/'))


def read_files_sha256(data):
    """files.sha256 bytes -> [(sha256 hex, path)] in file order, or ListError (device rules)."""
    entries, previous, line_length = [], None, 64 + 2 + LIST_LIMITS['path']
    lines = data.split(b'\n')
    for number, line in enumerate(lines[:-1], 1):
        if len(line) > line_length:
            raise ListError('line_too_long', number)
        if len(line) < 64 or not _HEX.fullmatch(line[:64]):
            raise ListError('bad_hex', number)
        if len(line) < 66 or line[64:65] != b' ' or line[65:66] not in (b' ', b'*'):
            raise ListError('syntax', number)
        path = line[66:]
        if not _list_path_ok(path):
            raise ListError('bad_path', number)
        if path == b'files.sha256' or b'bootloader' in path.lower():
            raise ListError('refused_path', number)
        if previous is not None and path <= previous:
            raise ListError('duplicate' if path == previous else 'order', number)
        if len(entries) == LIST_LIMITS['files']:
            raise ListError('too_many', number)
        entries.append((line[:64].decode(), path.decode()))
        previous = path
    if lines[-1]:
        if len(lines[-1]) > line_length:
            raise ListError('line_too_long', len(lines))
        raise ListError('no_newline', len(lines))
    if not entries:
        raise ListError('empty', 0)
    return entries


def files_sha256_text(folder, exclude=('files.sha256',)):
    """files.sha256 for every file under folder (sorted by path, bytewise), checked by read_files_sha256."""
    import hashlib

    folder = Path(folder)
    paths = sorted(
        p.relative_to(folder).as_posix().encode()
        for p in folder.rglob('*')
        if p.is_file() and p.relative_to(folder).as_posix() not in exclude
    )
    text = b''.join(
        b'%s  %s\n' % (hashlib.sha256((folder / p.decode()).read_bytes()).hexdigest().encode(), p) for p in paths
    )
    read_files_sha256(text)
    return text.decode()
