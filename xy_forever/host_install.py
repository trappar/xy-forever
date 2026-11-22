"""Read and validate install/rehearsal input on the host (no device access)."""

import hashlib
import json
import struct
from pathlib import Path

from xy_forever import usb_proto as P
from xy_forever.ldr import MAX_IMAGE, inspect_ldr

DESCRIPTOR = struct.pack('<II', 0x584C4348, 1)
MIN_IMAGE, IMAGE_CAPACITY = 0x10000, 0x700000
# Keep the historical USB fixture mode and the supported product/device-test modes.
USB_MODES = ('xyf-usb', 'xyf-product', 'xyf-mtp-usb', 'xyf-crash-trigger')


def load_build(target):
    """Returns (image bytes, description lines). Raises ValueError on any doubt."""
    path = Path(target)
    notes = []
    if path.is_dir():
        manifest = json.loads((path / 'manifest.json').read_text())
        verification = json.loads((path / 'verification.json').read_text())
        if not verification.get('passed'):
            raise ValueError('build verification did not pass')
        data = (path / 'candidate.ldr').read_bytes()
        if hashlib.sha256(data).hexdigest() != manifest['candidate_sha256']:
            raise ValueError('candidate.ldr does not match its manifest SHA-256')
        if manifest.get('mode') not in USB_MODES:
            # Installing a build without this USB service would end host access
            # until a dev-menu install (Test 043: product builds keep it).
            raise ValueError('build mode %r has no USB service; install it from the dev menu' % manifest.get('mode'))
        notes.append('build %s (mode %s, verified)' % (path, manifest.get('mode')))
    else:
        # docs/SAFETY.md: only builds whose --verify passed.
        raise ValueError('install needs a build directory whose verification passed, not a bare file: %s' % path)
    if not MIN_IMAGE <= len(data) <= min(IMAGE_CAPACITY, MAX_IMAGE):
        raise ValueError('size %d outside %d..%d' % (len(data), MIN_IMAGE, IMAGE_CAPACITY))
    if DESCRIPTOR not in data:
        raise ValueError('not an XY Forever build: cold descriptor missing')
    summary = inspect_ldr(data)
    notes.append('LDR ok: %d records, entry %s' % (summary['records'], summary['entry']))
    notes.append('size %d, sha256 %s' % (len(data), hashlib.sha256(data).hexdigest()))
    return data, notes


def load_stock(target):
    """UNINSTALL image: only the exact stock 1.2.1 os.ldr (the dev-menu restore
    image). Returns (bytes, notes); raises ValueError on any difference."""
    path = Path(target)
    if not path.is_file():
        raise ValueError('uninstall needs the stock os.ldr file: %s' % path)
    data = path.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    if len(data) != P.STOCK_OS_LDR_BYTES or sha != P.STOCK_OS_LDR_SHA256:
        raise ValueError('not the stock 1.2.1 os.ldr (size %d, sha256 %s); refusing' % (len(data), sha))
    summary = inspect_ldr(data)
    return data, [
        'stock 1.2.1 os.ldr %s' % path,
        'LDR ok: %d records, entry %s' % (summary['records'], summary['entry']),
        'size %d, sha256 %s' % (len(data), sha),
    ]
