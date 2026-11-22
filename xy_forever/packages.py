"""Offline, bounded package inspection and staging. No device I/O or TFW keys.

This accepts the decrypted official ZIP layout. A staged directory is a host
artifact, not a directory to mirror onto a device. Package hashes identify bytes;
they do not authenticate the vendor's signature or establish compatibility.
"""

from __future__ import annotations

import hashlib
import json
import stat
import unicodedata
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath


class PackageError(ValueError):
    pass


@dataclass(frozen=True)
class Limits:
    members: int = 4096
    member_bytes: int = 64 * 1024 * 1024
    total_bytes: int = 256 * 1024 * 1024
    archive_bytes: int = 256 * 1024 * 1024

    def __post_init__(self):
        if any(type(value) is not int or value <= 0 for value in asdict(self).values()):
            raise PackageError('package limits must be positive integers')


@dataclass(frozen=True)
class Member:
    source: str
    role: str
    destination: str | None
    size: int
    sha256: str


@dataclass(frozen=True)
class PackagePlan:
    archive_sha256: str
    archive_bytes: int
    total_bytes: int
    members: tuple[Member, ...]
    schema: int = 1

    def to_dict(self):
        return asdict(self)


def safe_name(name: str, *, directory: bool = False) -> str:
    """Reject aliases dangerous on both POSIX and common MTP host filesystems."""
    if directory and name.endswith('/'):
        name = name[:-1]
    if not name or len(name.encode('utf-8')) > 1024:
        raise PackageError('empty or oversized package name')
    if name != unicodedata.normalize('NFC', name):
        raise PackageError(f'noncanonical Unicode name: {name!r}')
    if any(ord(c) < 32 or ord(c) == 127 for c in name) or '\\' in name or ':' in name:
        raise PackageError(f'unsafe package name: {name!r}')
    parts = name.split('/')
    if any(p in ('', '.', '..') or p.endswith((' ', '.')) for p in parts):
        raise PackageError(f'unsafe package path: {name!r}')
    return name


_COMPONENTS = {
    'firmware/blackfin/bin/os.ldr': ('os', 'payload/os.ldr'),
    'firmware/blackfin/bin/os.version': ('metadata', 'metadata/os.version'),
    'firmware/backplane/bin/backplane.cyacd': ('backplane', 'companions/backplane.cyacd'),
    'firmware/backplane/bin/backplane.version': ('metadata', 'metadata/backplane.version'),
    'firmware/ble/bin/ble.bin': ('ble', 'companions/ble.bin'),
    'firmware/ble/bin/ble.version': ('metadata', 'metadata/ble.version'),
    'firmware/firmware.version': ('metadata', 'metadata/firmware.version'),
    'firmware/versions.txt': ('metadata', 'metadata/versions.txt'),
}
_CONTENT = 'firmware/blackfin/content/'


def destination_for(name: str) -> tuple[str, str | None]:
    safe_name(name)
    path = PurePosixPath(name)
    # Exclude bootloader components before the content rule as well. An unknown
    # bootloader file can never gain a destination through a content prefix.
    if any('bootloader' in part.casefold() for part in path.parts):
        return 'excluded-bootloader', None
    if path.name == '.DS_Store' or name.startswith('__MACOSX/'):
        return 'excluded-host-metadata', None
    if name in _COMPONENTS:
        return _COMPONENTS[name]
    if name.startswith(_CONTENT):
        relative = name[len(_CONTENT) :]
        if not relative:
            raise PackageError('empty content name')
        return 'content', 'content/' + relative
    raise PackageError(f'unsupported package member: {name}')


def _digest_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(65536), b''):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _read_member(archive, info, limit, sink=None):
    digest = hashlib.sha256()
    count = 0
    with archive.open(info) as stream:
        while True:
            chunk = stream.read(65536)
            if not chunk:
                break
            count += len(chunk)
            if count > limit or count > info.file_size:
                raise PackageError(f'expanded member exceeds declared/bounded size: {info.filename}')
            digest.update(chunk)
            if sink is not None:
                sink.write(chunk)
    if count != info.file_size:
        raise PackageError(f'member length mismatch: {info.filename}')
    return digest.hexdigest()


def _inventory(archive, limits):
    infos = archive.infolist()
    if len(infos) > limits.members:
        raise PackageError('package has too many members')
    names = {}
    total = 0
    files = []
    for info in infos:
        # ZipInfo truncates its public filename at NUL; check the original too.
        if info.orig_filename != info.filename:
            raise PackageError('NUL or transformed ZIP member name')
        directory = info.is_dir()
        name = safe_name(info.filename, directory=directory)
        key = name.casefold()
        if key in names:
            raise PackageError(f'duplicate or case-alias package member: {name}')
        names[key] = directory
        mode = info.external_attr >> 16
        kind = stat.S_IFMT(mode)
        if kind not in (0, stat.S_IFREG, stat.S_IFDIR) or ((kind == stat.S_IFDIR) != directory and kind != 0):
            raise PackageError(f'special-file package member: {name}')
        if info.flag_bits & 1:
            raise PackageError('encrypted ZIP members are unsupported; decrypt the TFW first')
        if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            raise PackageError('only stored and deflated ZIP members are supported')
        if directory:
            if info.file_size:
                raise PackageError('directory member contains data')
            continue
        if info.file_size > limits.member_bytes:
            raise PackageError(f'package member exceeds limit: {name}')
        total += info.file_size
        if total > limits.total_bytes:
            raise PackageError('expanded package exceeds limit')
        files.append(info)
    for name in names:
        parts = name.split('/')
        for end in range(1, len(parts)):
            if names.get('/'.join(parts[:end])) is False:
                raise PackageError(f'package file/directory conflict: {name}')
    return files, total


def inspect_package(path, *, limits=Limits()) -> PackagePlan:
    """Hash and CRC-check every member, including excluded bootloaders."""
    path = Path(path)
    if path.stat().st_size > limits.archive_bytes:
        raise PackageError('archive exceeds limit')
    archive_hash, archive_size = _digest_file(path)
    members = []
    try:
        with zipfile.ZipFile(path) as archive:
            infos, total = _inventory(archive, limits)
            for info in infos:
                role, destination = destination_for(info.filename)
                digest = _read_member(archive, info, limits.member_bytes)
                members.append(Member(info.filename, role, destination, info.file_size, digest))
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as error:
        raise PackageError(f'invalid package: {error}') from error
    if sum(member.role == 'os' for member in members) != 1:
        raise PackageError('package must contain exactly firmware/blackfin/bin/os.ldr')
    if _digest_file(path) != (archive_hash, archive_size):
        raise PackageError('archive changed during inspection')
    return PackagePlan(archive_hash, archive_size, total, tuple(members))


def stage_package(path, output, *, limits=Limits()) -> PackagePlan:
    """Create a fresh host staging tree. Manifest is published only on success.

    An interrupted output retains INCOMPLETE and is never accepted by storage.
    No device path is opened; never mirror this tree onto a device root.
    """
    path, output = Path(path), Path(output)
    plan = inspect_package(path, limits=limits)
    output.mkdir(parents=True, exist_ok=False)
    marker = output / 'INCOMPLETE'
    marker.write_text('Host staging incomplete; do not activate.\n', encoding='utf-8')
    if _digest_file(path) != (plan.archive_sha256, plan.archive_bytes):
        raise PackageError('archive changed before staging')
    try:
        with zipfile.ZipFile(path) as archive:
            for member in plan.members:
                if member.destination is None:
                    continue
                target = output / member.destination
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open('xb') as sink:
                    digest = _read_member(archive, archive.getinfo(member.source), limits.member_bytes, sink)
                if digest != member.sha256:
                    raise PackageError('archive changed during staging')
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as error:
        raise PackageError(f'invalid package: {error}') from error
    if _digest_file(path) != (plan.archive_sha256, plan.archive_bytes):
        raise PackageError('archive changed during staging')
    (output / 'stage.json').write_text(json.dumps(plan.to_dict(), indent=2) + '\n', encoding='utf-8')
    marker.unlink()
    return plan
