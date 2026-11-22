"""OS-only extraction from an official .tfw on the host (docs/hardware/tfw.md).

Runs the device's own freestanding extractor (native/tfw_os.cpp) and signature check
(native/tfw_signature.cpp, native/p224.cpp), compiled for the Mac with host/tfw_extract.cpp as
its adapter: no Python decoder. The .tfw key and the vendor public key come from the private area
(keys/opxy-update-keys.json by key index, keys/opxy-tfw-public-key.json) and reach the extractor
on a pipe; they are never printed, logged or written.

The signature is required by default: without the public key extraction stops ("no .tfw public
key"). require_signature=False (xyf-package --allow-unverified) extracts anyway, reports the
signature as not checked and labels the entry kind=custom, not official.
"""

import json
import struct
import subprocess
import tempfile
import zlib
from pathlib import Path

from . import package_records, paths
from .xyf_entry import firmware_document

ROOT = Path(__file__).resolve().parents[1]
CXX = '/usr/bin/clang++'
KEYS = paths.PRIVATE_ROOT / 'keys' / 'opxy-update-keys.json'
PUBLIC_KEY = paths.PRIVATE_ROOT / 'keys' / 'opxy-tfw-public-key.json'
NATIVE = ('tfw_os', 'tfw', 'tfw_check', 'tfw_signature', 'p224', 'aes', 'hash', 'inflate', 'ldr', 'package_policy')
VERIFIED = 'verified'
# The policy file boot admits images with: cold.cpp's ranges, with the product's wider
# module + stack reservation (xyf-build --xyf-product).
PRODUCT_STACK_END = '0x8ab84000u'


class ExtractError(ValueError):
    pass


def build(directory, flags=('-O2',), run=subprocess.run):
    """Compile the host extractor into directory; returns the executable's path. run: the tests
    pass xy_forever.hostbuild.run (a cached compile)."""
    from .sim import policy_source  # cold.cpp's allowed and protected ranges, verbatim

    directory = Path(directory)
    (directory / 'cold_policy.inc').write_text(policy_source())
    binary = directory / 'tfw_extract'
    command = [
        CXX,
        '-std=c++11',
        *flags,
        '-Wall',
        '-Wextra',
        '-Werror',
        '-DXYF_STACK_END=' + PRODUCT_STACK_END,
        '-I',
        str(directory),
        str(ROOT / 'host/tfw_extract.cpp'),
        *[str(ROOT / 'native' / (name + '.cpp')) for name in NATIVE],
        '-o',
        str(binary),
    ]
    result = run(command, capture_output=True, text=True)
    if result.returncode:
        raise ExtractError('host extractor build failed:\n' + result.stderr[-2000:])
    return binary


def key_records(path=None):
    """The private .tfw keys as 33-byte records (index, key) for the extractor's stdin."""
    path = Path(path) if path else KEYS
    if not path.is_file():
        raise ExtractError(
            'no .tfw key: %s is missing (the private area holds the keys; set XYF_PRIVATE or pass --keys)' % path
        )
    try:
        data = json.loads(path.read_text())
        records = b''
        for key in data.get('keys', ()):
            if key.get('read_ok') and isinstance(key.get('index'), int) and 0 <= key['index'] < 256:
                value = bytes.fromhex(key.get('hex', ''))
                if len(value) == 32:
                    records += bytes([key['index']]) + value
    except ValueError, TypeError, AttributeError:
        raise ExtractError('no .tfw key: %s is not a readable key file' % path) from None
    if not records:
        raise ExtractError('no .tfw key: %s holds no usable key' % path)
    return records


def public_key(path=None):
    """The vendor public key (56 bytes, Qx || Qy big-endian) from the private area."""
    path = Path(path) if path else PUBLIC_KEY
    if not path.is_file():
        raise ExtractError(
            'no .tfw public key: %s is missing, so the package signature cannot be checked '
            '(uv run xyf-package extract-os --allow-unverified extracts it unchecked)' % path
        )
    try:
        data = json.loads(path.read_text())
        value = bytes.fromhex(data['hex'])
        if data.get('curve') != 'P-224' or len(value) != 56:
            raise ValueError
    except ValueError, TypeError, KeyError, AttributeError:
        raise ExtractError('no .tfw public key: %s is not a readable P-224 public key file' % path) from None
    return value


def run(binary, command, package, *args, keys=b'', public_key=None):
    """One extractor run; returns its JSON report. public_key (56 bytes) selects verification;
    otherwise args must name another signature mode (--unverified, --reject-signature)."""
    if public_key is not None:
        args, keys = (*args, '--public-key-stdin'), bytes(public_key) + keys
    result = subprocess.run([str(binary), command, str(package), *map(str, args)], input=keys, capture_output=True)
    try:
        return json.loads(result.stdout)
    except ValueError:
        raise ExtractError('host extractor failed: %s' % result.stderr.decode(errors='replace')[-500:]) from None


def entry_name(version):
    return 'OP-XY ' + version.split('+')[0]


def extract_os(package, out, *, keys=None, public_key_file=None, require_signature=True, name=None, binary=None):
    """Write out/os.ldr and out/manifest.json (docs/STORAGE.md) from an official .tfw.

    The header signature is verified with the vendor public key; without the key this fails
    unless require_signature is False, and then the report says 'not checked' and the entry is
    kind=custom. out must not exist (an entry is never overwritten). On any failure nothing is
    left behind."""
    package, out = Path(package), Path(out)
    if out.exists():
        raise ExtractError('output folder already exists: %s' % out)
    try:
        vendor = public_key(public_key_file)
    except ExtractError:
        if require_signature:
            raise
        vendor = None
    records = key_records(keys)
    with tempfile.TemporaryDirectory(prefix='xyf-tfw-extract-') as work:
        binary = binary or build(work)
        out.mkdir(parents=True)
        partial = out / 'os.ldr.partial'
        try:
            report = run(
                binary,
                'extract',
                package,
                partial,
                *(() if vendor else ('--unverified',)),
                keys=records,
                public_key=vendor,
            )
            if report.get('status') != 'ok':
                details = [
                    '%s=%s' % (k, report.get(k)) for k in ('container', 'inflate', 'ldr') if report.get(k) != 'ok'
                ]
                if report.get('container') == 'signature':
                    details.append('signature=%s' % report.get('signature'))
                raise ExtractError('extraction refused: %s (%s)' % (report.get('status'), ', '.join(details)))
            if (report.get('signature') == VERIFIED) != bool(vendor):
                raise ExtractError('signature state differs from the request: %s' % report.get('signature'))
            data = partial.read_bytes()
            if (len(data), '%08x' % (zlib.crc32(data) & 0xFFFFFFFF)) != (report['bytes'], report['crc32']):
                raise ExtractError('written os.ldr differs from what the extractor checked')
            partial.rename(out / 'os.ldr')
            (out / package_records.NATIVE).write_bytes(
                firmware_document(
                    name or entry_name(report['version']), report['version'], 'official' if vendor else 'custom', data
                )
            )
        except BaseException:
            for path in (partial, out / 'os.ldr', out / package_records.NATIVE):
                if path.exists():
                    path.unlink()
            out.rmdir()
            raise
    report['folder'] = str(out)
    return report


CUT_MAGIC, CUT_HEAD = 0x54434658, 0x480  # "XFCT"; the wrapper, crypto header and name block
CUT_TAIL = 256 * 1024  # the official directories are ~80 KB


def cut(package, tail=CUT_TAIL, head=CUT_HEAD):
    """A cut of a .tfw (native/tfw_check.hpp): a 16-byte header {magic, package bytes, head, tail
    offset}, the package's first head bytes and its bytes from the tail offset on, the offset
    16-byte aligned. Holds everything tfw.check reads (header, name block, last block, ZIP
    directory) when the directory fits the tail. package: a path or bytes. Ciphertext only."""
    if isinstance(package, (bytes, bytearray)):
        size, read = len(package), lambda at, n: bytes(package[at : at + n])
    else:
        path = Path(package)
        size = path.stat().st_size

        def read(at, n):
            with path.open('rb') as f:
                f.seek(at)
                return f.read(n)

    start = max(head, (size - tail) & ~15)
    return struct.pack('<4I', CUT_MAGIC, size, head, start) + read(0, head) + read(start, size - start)
