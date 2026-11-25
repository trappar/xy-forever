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
PACK_RECORDS = ROOT + '/pack-records'  # an imported pack's records, beside its folder (target/xyf_blob.hpp)
PACK_REFERENCES = '/xyf-pack-v1.sha256'
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


def unless_busy(dev, request, seconds=BUSY_S, every=0.05):
    """request()'s result (its status first), sent again while the device answers busy (a write window settling
    after a change, a job's step) for at most seconds, every seconds apart, on the device's clock (the simulator's,
    in tests)."""
    for tries in range(int(seconds / every), -1, -1):
        result = request()
        if result[0] != P.ids.S_BUSY or not tries:
            return result
        pause(getattr(dev, 't', None), every)


class Session:
    def __init__(self, dev, sweep=True):
        self.dev = dev
        self.sweep_on_enter = sweep
        self.token = secrets.token_bytes(32)

    def command(self, action):
        code, _ = self.dev.call(P.ids.PACKAGE_SESSION, struct.pack('<II', 1, action) + self.token)
        check(code, 'package.session')

    def __enter__(self):
        request = struct.pack('<II', 1, 0) + self.token
        code, _ = unless_busy(self.dev, lambda: self.dev.call(P.ids.PACKAGE_SESSION, request))
        check(code, 'package.session')
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
        code, _ = unless_busy(self.dev, lambda: self.dev.list_dir(ROOT), every=0.02)
        check(code, 'package window close')

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

    def sha256(self, path, size=0, optional=False):
        """The SHA-256 digest of the file stored at path (None: missing, with optional); size: the bytes expected. Hashed
        on the device (file.hash, 1.32: nothing comes back over USB), else read back whole and hashed here."""
        if not host_write.hashes(self.dev):
            data = self.read(path, optional)
            return None if data is None else hashlib.sha256(data).digest()
        self.command(1)
        code, sha = host_write.stored_sha256(self.dev, path, size)
        if optional and code == MISSING:
            return None
        check(code, 'hash %s' % path)
        return sha

    def put(self, path, data):
        """Writes data to path. The device checks the bytes it received (CRC-32, SHA-256) and the file it read back
        (CRC-32) before it renames the file onto path (host_write.put)."""
        self.command(1)
        if depth.refusal(path):
            raise StoreError(depth.refusal(path))
        code = host_write.put(self.dev, data, path, say=lambda _: None)
        check(code, 'write %s' % path)

    def write(self, path, data):
        """put, then the stored file checked by its SHA-256."""
        self.put(path, data)
        if self.sha256(path, len(data)) != hashlib.sha256(data).digest():
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

    def retain(self, references, sources=None, *, factory=False):
        """Retain missing hashes. An exact live factory file already satisfies its reference."""
        sources = sources or {}
        existing = {name.decode('ascii') for kind, _, name in self.entries(BLOBS) if kind == 0 and name.isascii()}
        live_hashes = set()
        for sha, rel in references:
            live_path = '/yaffs2/content/' + rel
            want = bytes.fromhex(sha)
            live = self.sha256(live_path, optional=True) if factory else None
            if factory and rel == 'op1_factory.json' and live != want:
                live = self.sha256(ROOT + '/content-official/op1_factory.json', optional=True)
            if live == want:
                live_hashes.add(sha)
                continue
            if sha in existing or sha in live_hashes:
                continue
            data = sources.get(rel)
            if data is None and not factory:
                data = self.read(live_path, optional=True)
            if data is None or hashlib.sha256(data).hexdigest() != sha:
                raise StoreError('content unavailable or differs: %s (%s)' % (rel, sha))
            self.write(BLOBS + '/' + sha, data)
            existing.add(sha)
        duplicates = live_hashes & existing
        if duplicates:
            # Mod entries and recorded packs may also need these hashes by their blob name.
            for sha in sorted(duplicates - self.references(include_factory=False)):
                self.change(P.ids.DELETE_FILE, BLOBS + '/' + sha)

    def publish(self, target, files, keep=()):
        """Publishes files ({rel: bytes}) as target. keep: rels the published target already holds with these bytes (the
        caller checked them): moved over from it on the device after the rest is staged and checked, never sent."""
        if not package_name(target.rsplit('/', 1)[-1]):
            raise StoreError('unsafe package name')
        if not target.startswith((LIBRARY + '/', ROOT + '/mods/')):
            raise StoreError('unsupported package destination')
        # Validate every final and temporary path before the first staged byte.
        scratch = self.scratch()
        for rel in files:
            if depth.refusal(scratch + '/' + rel) or depth.refusal(target + '/' + rel):
                raise StoreError('package exceeds storage depth')
        sent = {rel: data for rel, data in files.items() if rel not in keep}
        for rel, data in sorted(sent.items()):
            self.put(scratch + '/' + rel, data)
        # Check the entire staged result, as stored now, before the sole publication rename: one SHA-256 per file, hashed
        # on the device from 1.32 (#327), else read back over USB.
        for rel, data in sorted(sent.items()):
            if self.sha256(scratch + '/' + rel, len(data)) != hashlib.sha256(data).digest():
                raise StoreError('staged package changed: %s' % rel)
        old = scratch + '-old'
        parent, name = target.rsplit('/', 1)
        present = any(raw == name.encode() for _, _, raw in self.entries(parent))
        if keep and not present:
            raise StoreError('%s is gone: nothing to keep from it' % target)
        if present:
            self.rename(target, old)
        # The kept files move over only now: the target is away just for these renames (a cut leaves the old package in
        # tmp/, swept, and the next push sends everything).
        for rel in sorted(keep):
            self.rename(old + '/' + rel, scratch + '/' + rel)
        self.rename(scratch, target)
        if present:
            self.erase(old)

    def remove(self, target):
        scratch = self.scratch()
        # Remove the empty placeholder; target is detached by one rename.
        self.change(P.ids.REMOVE_DIR, scratch)
        self.rename(target, scratch)
        self.erase(scratch)

    def references(self, *, include_factory=True):
        keep = set()
        for kind, _, name in self.entries(LIBRARY):
            if kind != 1:
                continue
            folder = LIBRARY + '/' + name.decode('utf-8', 'strict')
            for manifest in ('content.sha256', 'files.sha256') if include_factory else ('files.sha256',):
                text = self.read(folder + '/' + manifest, optional=True)
                if text is None:
                    continue
                for sha, rel in read_files_sha256(text):
                    if manifest == 'content.sha256' or rel.startswith('content/'):
                        keep.add(sha)
        for kind, _, name in self.entries(PACK_RECORDS):
            if kind != 1:
                continue
            data = self.read(PACK_RECORDS + '/' + name.decode('utf-8', 'strict') + PACK_REFERENCES, optional=True)
            if data is not None:
                keep.update(sha for sha, _ in read_files_sha256(data))
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
