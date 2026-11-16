"""Content-set manifests (plan 4.6a1, docs/decisions/010-package-model.md, docs/STORAGE.md).

A content set is every file of an official package's `firmware/blackfin/content/` folder, which
the OS reads as `/yaffs2/content/`. Its manifest is `content.sha256`: the files.sha256 list form
(paths relative to the content folder, sorted bytewise, checked by the device reader's host twin
`read_files_sha256`). Beside it the host writes `content.sizes` (host only: `<bytes>  <path>`, the
same lines in the same order) so a compare can give byte totals, and a copy of the package's
`firmware.version`. Offline only; never opens a device.
"""
import hashlib
import os
from pathlib import Path
import stat
import sys

from .xyf_entry import LIST_LIMITS, ListError, _list_path_ok, read_files_sha256

LIST = 'content.sha256'
SIZES = 'content.sizes'
VERSION = 'firmware.version'


class ContentError(ValueError):
    pass


def path_problem(rel):
    """Why the device list reader refuses this relative path (bytes), or None."""
    if not 0 < len(rel) <= LIST_LIMITS['path']:
        return 'longer than %d bytes' % LIST_LIMITS['path'] if rel else 'empty path'
    if any(not 0x20 <= c <= 0x7e or c in b'\\:' for c in rel):
        return 'a byte outside printable ASCII, or \\ or :'
    if not _list_path_ok(rel):
        return 'an empty, "." or ".." name, or a name ending in "." or space'
    if rel == b'files.sha256' or b'bootloader' in rel.lower():
        return 'a refused name (files.sha256 at the top, or containing "bootloader")'
    return None


def check_paths(paths):
    """Relative paths (bytes) -> sorted list; ContentError for any rule the device list enforces,
    for two paths equal after normalisation (ASCII case folded: one file on a case-insensitive
    disk, such as the Mac's), and for an empty set."""
    bad = [(p, path_problem(p)) for p in paths]
    bad = [(p, why) for p, why in bad if why]
    if bad:
        raise ContentError('%d path(s) the device list refuses; first: %r (%s)' % (len(bad), bad[0][0], bad[0][1]))
    if not paths:
        raise ContentError('empty content set: no files')
    seen = {}
    for p in sorted(paths):
        key = p.lower()
        if key in seen:
            raise ContentError('duplicate paths after normalisation (case folded): %r and %r' % (seen[key], p))
        seen[key] = p
    if len(paths) > LIST_LIMITS['files']:
        raise ContentError('%d files: the device list holds at most %d' % (len(paths), LIST_LIMITS['files']))
    return sorted(paths)


def scan(folder):
    """Content folder -> [(path bytes, bytes, sha256 hex)] sorted by path; refuses symlinks and
    anything that is not a regular file or folder."""
    folder = Path(folder)
    if folder.is_symlink() or not folder.is_dir():
        raise ContentError('not a folder: %s' % folder)
    found = {}
    for root, dirs, files in os.walk(folder):
        for name in sorted(dirs + files):
            full = Path(root) / name
            mode = os.lstat(full).st_mode
            rel = full.relative_to(folder).as_posix()
            if stat.S_ISLNK(mode):
                raise ContentError('symlink refused: %s' % rel)
            if stat.S_ISREG(mode):
                found[os.fsencode(rel)] = full
            elif not stat.S_ISDIR(mode):
                raise ContentError('not a regular file: %s' % rel)
    entries = []
    for rel in check_paths(list(found)):
        digest, size = hashlib.sha256(), 0
        with open(found[rel], 'rb') as f:
            for block in iter(lambda: f.read(1 << 20), b''):
                digest.update(block)
                size += len(block)
        entries.append((rel, size, digest.hexdigest()))
    return entries


def list_text(entries):
    """The content.sha256 bytes for scan()'s entries; accepted by read_files_sha256 or refused."""
    data = b''.join(b'%s  %s\n' % (h.encode(), p) for p, _, h in entries)
    try:
        read_files_sha256(data)
    except ListError as error:
        raise ContentError('device list reader refuses the list: %s' % error)
    return data


def sizes_text(entries):
    return b''.join(b'%d  %s\n' % (n, p) for p, n, _ in entries)


def read_version(folder, version_file=None):
    """The package's own version file (default: <content>/../../firmware.version, the package
    layout firmware/{firmware.version, blackfin/content/}) -> (raw bytes, stripped text)."""
    path = Path(version_file) if version_file else Path(folder).parent.parent / VERSION
    if not path.is_file():
        raise ContentError('no version file: %s (give --version-file)' % path)
    raw = path.read_bytes()
    text = raw.strip()
    if not 0 < len(text) <= 31 or any(not 0x21 <= c <= 0x7e for c in text):
        raise ContentError('version file is not one printable token of 1-31 bytes: %s' % path)
    return raw, text.decode()


def summary_line(version, entries, data):
    return 'version=%s files=%d bytes=%d list_sha256=%s' % (
        version, len(entries), sum(n for _, n, _ in entries), hashlib.sha256(data).hexdigest())


def outside_checkouts(path):
    path = Path(path).expanduser().resolve()
    if any((p / '.git').exists() for p in (path, *path.parents)):
        raise ContentError('manifests of official content stay outside every git checkout: %s' % path)
    return path


def write_manifest(folder, out, version_file=None):
    """Writes out/{content.sha256, content.sizes, firmware.version}; returns the summary line."""
    raw, version = read_version(folder, version_file)
    entries = scan(folder)
    data = list_text(entries)
    out = outside_checkouts(out)
    out.mkdir(parents=True, exist_ok=True)
    (out / LIST).write_bytes(data)
    (out / SIZES).write_bytes(sizes_text(entries))
    (out / VERSION).write_bytes(raw)
    return summary_line(version, entries, data)


def load(source):
    """A content folder or a content.sha256 file -> {path: (sha256 hex, bytes or None)}. A list's
    sizes come from content.sizes beside it (same stem), when present."""
    source = Path(source)
    if source.is_dir():
        return {p.decode(): (h, n) for p, n, h in scan(source)}
    try:
        listed = read_files_sha256(source.read_bytes())
    except ListError as error:
        raise ContentError('%s: %s' % (source, error))
    sizes = {}
    sidecar = source.with_suffix('.sizes')
    if sidecar.is_file():
        lines = sidecar.read_bytes().decode('ascii').splitlines()
        pairs = [line.split('  ', 1) for line in lines]
        if [p[1:] for p in pairs] != [[p] for _, p in listed] or not all(p[0].isdigit() for p in pairs):
            raise ContentError('%s does not match %s' % (sidecar, source))
        sizes = {p: int(n) for n, p in pairs}
    return {p: (h, sizes.get(p)) for h, p in listed}


def compare(a, b):
    """Two load() results -> {'only_a', 'only_b', 'differ'}: each {count, bytes / bytes_a+bytes_b,
    paths}; a byte total is None when a size is unknown (a list without content.sizes)."""
    def total(paths, side, index=1):
        sizes = [side[p][index] for p in paths]
        return None if None in sizes else sum(sizes)
    only_a = sorted(set(a) - set(b), key=str.encode)
    only_b = sorted(set(b) - set(a), key=str.encode)
    differ = sorted((p for p in set(a) & set(b) if a[p][0] != b[p][0]), key=str.encode)
    return dict(only_a=dict(count=len(only_a), bytes=total(only_a, a), paths=only_a),
                only_b=dict(count=len(only_b), bytes=total(only_b, b), paths=only_b),
                differ=dict(count=len(differ), bytes_a=total(differ, a), bytes_b=total(differ, b), paths=differ),
                identical=not (only_a or only_b or differ))


def main(argv):
    import argparse
    import json
    parser = argparse.ArgumentParser(prog='xyf_package.py content-manifest|content-compare', description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    make = commands.add_parser('content-manifest', help='write a content set manifest')
    make.add_argument('content', type=Path, help='extracted official content folder (firmware/blackfin/content)')
    make.add_argument('--out', required=True, type=Path, help='output folder outside every git checkout')
    make.add_argument('--version-file', type=Path, help='default: <content>/../../firmware.version')
    diff = commands.add_parser('content-compare', help='compare two manifests or folders; exit 0 when identical')
    diff.add_argument('a', type=Path, help='content.sha256 or content folder')
    diff.add_argument('b', type=Path, help='content.sha256 or content folder')
    diff.add_argument('--paths', action='store_true', help='also list the paths')
    args = parser.parse_args(argv)
    try:
        if args.command == 'content-manifest':
            print(write_manifest(args.content, args.out, args.version_file))
            return 0
        result = compare(load(args.a), load(args.b))
    except (ContentError, OSError) as error:
        print('error: %s' % error, file=sys.stderr)
        return 2
    if not args.paths:
        for key in ('only_a', 'only_b', 'differ'):
            del result[key]['paths']
    print(json.dumps(result, indent=2))
    return 0 if result['identical'] else 1
