"""Package transactions over the existing file service (session ABI 1).

Only scratch folders are visible until one directory rename publishes a checked
package. Removal commits by moving it out of the published tree. A session holds
the device's existing job exclusion so MTP, sweep and content jobs cannot race
validation or the reachability snapshot used by blob collection.
"""

import contextlib
import hashlib
import re
import secrets
import struct

from . import depth, host_write, package_records
from . import usb_proto as P
from .transport import pause
from .xyf_entry import read_files_sha256

BUSY_S = 60  # how long a busy device is waited for (its time)

ROOT = '/yaffs2/xy-forever'
TMP = ROOT + '/tmp'
BLOBS = ROOT + '/blobs'
LIBRARY = ROOT + '/firmware'
PACK_ROOTS = (ROOT + '/packs', '/yaffs2/content/dlc')
PACK_REFERENCES = '/xyf-blobs-v1.sha256'
HEX = re.compile(r'[0-9a-f]{64}\Z')
MISSING = P.ids.S_NOT_FOUND


class StoreError(IOError):
    pass


def check(code, operation):
    if code:
        raise StoreError('%s: %s' % (operation, P.status_name(code)))


def list_text(files):
    text = ''.join(
        '%s  %s\n' % (hashlib.sha256(data).hexdigest(), path) for path, data in sorted(files.items())
    ).encode('ascii')
    read_files_sha256(text)
    return text


def package_name(name):
    return (
        1 <= len(name) <= 127
        and name[-1] not in '. '
        and 'bootloader' not in name.lower()
        and all(0x20 <= ord(c) <= 0x7E and c not in '/\\:' for c in name)
    )


def validate(files):
    """Strict admission before any write. The boot/library reader remains lenient."""
    listed = {path: sha for sha, path in read_files_sha256(files['files.sha256'])}
    if set(files) != set(listed) | {'files.sha256'}:
        raise StoreError('files.sha256 does not own exactly the staged package')
    for path, sha in listed.items():
        if hashlib.sha256(files[path]).hexdigest() != sha:
            raise StoreError('%s: SHA-256 differs' % path)
    name = package_records.record_name(files)
    if name is None:
        raise StoreError('no manifest.json or info.txt')
    fields = package_records.parse(files[name], name)
    if fields.get('type') in (None, 'firmware'):
        import zlib

        image = files.get('os.ldr')
        if (
            image is None
            or fields.get('bytes') != len(image)
            or fields.get('sha256') != hashlib.sha256(image).hexdigest()
            or fields.get('crc32') != '%08x' % (zlib.crc32(image) & 0xFFFFFFFF)
        ):
            raise StoreError('firmware integrity fields differ from os.ldr')
    if ('content.sha256' in files) != ('firmware.version' in files):
        raise StoreError('content.sha256 and firmware.version must come together')
    if 'content.sha256' in files:
        read_files_sha256(files['content.sha256'])
    return fields


class Session:
    def __init__(self, dev, sweep=True):
        self.dev = dev
        self.sweep_on_enter = sweep
        self.token = secrets.token_bytes(32)

    def command(self, action):
        code, _ = self.dev.call(P.ids.PACKAGE_SESSION, struct.pack('<II', 1, action) + self.token)
        check(code, 'package.session')

    def __enter__(self):
        for tries in range(BUSY_S * 20, -1, -1):  # 50 ms apart, on the device's clock (the simulator's, in tests)
            code, _ = self.dev.call(P.ids.PACKAGE_SESSION, struct.pack('<II', 1, 0) + self.token)
            if code != P.ids.S_BUSY or not tries:
                check(code, 'package.session')
                break
            pause(getattr(self.dev, 't', None), 0.05)
        if self.sweep_on_enter:
            self.sweep()
        return self

    def __exit__(self, kind, value, traceback):
        if kind:
            with contextlib.suppress(OSError, ValueError):
                self.finish()
        else:
            self.finish()

    def finish(self):
        self.command(2)
        # The close resynchronizes A's library view. USB remains live meanwhile.
        for tries in range(BUSY_S * 50, -1, -1):  # 20 ms apart, on the device's clock
            code, _ = self.dev.list_dir(ROOT)
            if code != P.ids.S_BUSY or not tries:
                check(code, 'package window close')
                break
            pause(getattr(self.dev, 't', None), 0.02)

    def read(self, path, optional=False):
        self.command(1)
        code, data = self.dev.read_all(path, limit=64 * 1024 * 1024 + 1024)
        if optional and code == MISSING:
            return None
        check(code, 'read %s' % path)
        return data

    def entries(self, path):
        self.command(1)
        code, entries = self.dev.list_dir(path)
        if code == MISSING:
            return []
        check(code, 'list %s' % path)
        return entries

    def write(self, path, data):
        self.command(1)
        if depth.refusal(path):
            raise StoreError(depth.refusal(path))
        code = host_write.put(self.dev, data, path, say=lambda _: None)
        check(code, 'write %s' % path)
        if hashlib.sha256(self.read(path)).digest() != hashlib.sha256(data).digest():
            raise StoreError('read-back SHA-256 differs: %s' % path)

    def change(self, kind, path):
        self.command(1)
        code, error = self.dev.change(kind, path)
        if kind == P.ids.MAKE_DIR and code == P.ids.S_STORAGE and error == 17:
            return
        check(code, 'change %s (errno %d)' % (path, error))

    def rename(self, source, target):
        self.command(1)
        code, error = self.dev.rename(source, target)
        check(code, 'rename %s -> %s (errno %d)' % (source, target, error))

    def erase(self, folder):
        for kind, _, raw in self.entries(folder):
            name = raw.decode('utf-8', 'strict')
            if not name or name in ('.', '..') or '/' in name:
                raise StoreError('unsafe directory entry')
            path = folder + '/' + name
            if kind == 1:
                self.erase(path)
            elif kind == 0:
                self.change(P.ids.DELETE_FILE, path)
            else:
                raise StoreError('package scratch contains a non-file: %s' % path)
        self.change(P.ids.REMOVE_DIR, folder)

    def sweep(self):
        for kind, _, name in self.entries(TMP):
            if kind == 1 and name.startswith(b'pkg-v1-'):
                self.erase(TMP + '/' + name.decode('ascii'))

    def scratch(self):
        # Versioned names identify only this transaction's disposable scratch.
        self.change(P.ids.MAKE_DIR, TMP)
        name = TMP + '/pkg-v1-' + secrets.token_hex(8)
        self.change(P.ids.MAKE_DIR, name)
        return name

    def retain(self, references, sources=None):
        """Verify named bytes before a package becomes reachable; duplicates share one blob."""
        sources = sources or {}
        for sha, rel in references:
            existing = self.read(BLOBS + '/' + sha, optional=True)
            if existing is not None:
                if hashlib.sha256(existing).hexdigest() != sha:
                    raise StoreError('corrupt content blob: %s' % sha)
                continue
            data = sources.get(rel)
            if data is None:
                data = self.read('/yaffs2/content/' + rel, optional=True)
            if data is None or hashlib.sha256(data).hexdigest() != sha:
                raise StoreError('content unavailable or differs: %s (%s)' % (rel, sha))
            self.write(BLOBS + '/' + sha, data)

    def publish(self, target, files):
        if not package_name(target.rsplit('/', 1)[-1]):
            raise StoreError('unsafe package name')
        if not target.startswith((LIBRARY + '/', ROOT + '/mods/')):
            raise StoreError('unsupported package destination')
        # Validate every final and temporary path before the first staged byte.
        scratch = self.scratch()
        for rel in files:
            if depth.refusal(scratch + '/' + rel) or depth.refusal(target + '/' + rel):
                raise StoreError('package exceeds storage depth')
        for rel, data in sorted(files.items()):
            self.write(scratch + '/' + rel, data)
        # Re-read the entire staged result before the sole publication rename.
        for rel, data in sorted(files.items()):
            if self.read(scratch + '/' + rel) != data:
                raise StoreError('staged package changed: %s' % rel)
        old = scratch + '-old'
        parent, name = target.rsplit('/', 1)
        present = any(raw == name.encode() for _, _, raw in self.entries(parent))
        if present:
            self.rename(target, old)
        self.rename(scratch, target)
        if present:
            self.erase(old)

    def remove(self, target):
        scratch = self.scratch()
        # Remove the empty placeholder; target is detached by one rename.
        self.change(P.ids.REMOVE_DIR, scratch)
        self.rename(target, scratch)
        self.erase(scratch)

    def references(self):
        keep = set()
        for kind, _, name in self.entries(LIBRARY):
            if kind != 1:
                continue
            folder = LIBRARY + '/' + name.decode('utf-8', 'strict')
            for manifest in ('content.sha256', 'files.sha256'):
                text = self.read(folder + '/' + manifest, optional=True)
                if text is None:
                    continue
                for sha, rel in read_files_sha256(text):
                    if manifest == 'content.sha256' or rel.startswith('content/'):
                        keep.add(sha)
        for root in PACK_ROOTS:
            for kind, _, name in self.entries(root):
                if kind != 1:
                    continue
                folder = root + '/' + name.decode('utf-8', 'strict')
                data = self.read(folder + PACK_REFERENCES, optional=True)
                if data is None:
                    continue
                rows = data.split(b'\n')
                if rows[-1] or not 2 <= len(rows) <= 4097:
                    raise StoreError('invalid pack blob references in %s' % folder)
                for row in rows[:-1]:
                    sha = row.decode('ascii', 'strict')
                    if not HEX.fullmatch(sha):
                        raise StoreError('invalid pack blob reference in %s' % folder)
                    keep.add(sha)
        return keep

    def collect(self):
        # Complete, strict reachability snapshot first. A malformed/newer list
        # aborts before any deletion; the session prevents concurrent mutation.
        if (
            self.read(ROOT + '/job.txt', optional=True) is not None
            or self.read(ROOT + '/job.old', optional=True) is not None
        ):
            return 0  # unresolved intent retains every content blob until replay or undo completes
        keep = self.references()
        removed = 0
        for kind, _, name in self.entries(BLOBS):
            sha = name.decode('ascii', 'strict')
            if kind == 0 and HEX.fullmatch(sha) and sha not in keep:
                self.change(P.ids.DELETE_FILE, BLOBS + '/' + sha)
                removed += 1
        return removed
