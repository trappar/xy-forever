"""Read-only directory listings and live content comparison for xyf_host.py."""

import hashlib
import json
import struct
import sys
import time

from . import content_manifest, paths
from . import usb_proto as P


def configure(sub):
    ls = sub.add_parser('ls', help='list a device folder: type, size, name (file.list, read-only)')
    ls.add_argument('path')
    ls.add_argument('--json', action='store_true')
    content = sub.add_parser('content', help='inspect the device content folder')
    content.add_subparsers(required=True).add_parser(
        'status', help='hash the live files and compare them with the private official content manifests'
    )


def list_directory(dev, path, as_json):
    code, entries = dev.list_dir(path)
    if code:
        print('ls %s: %s' % (path, P.status_name(code)))
        return code
    kinds = ('file', 'dir', 'other')
    rows = [
        dict(name=name.decode('utf-8', 'replace'), type=kinds[k] if k < len(kinds) else 'kind_%d' % k, size=size)
        for k, size, name in entries
    ]
    if as_json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
    else:
        for row in rows:
            print('%-5s %10s  %s' % (row['type'], row['size'] if row['type'] == 'file' else '-', row['name']))
    return 0


def free_bytes(dev):
    command = P.ids.STORAGE_SPACE
    code, body = dev.call(command, struct.pack('<I', 1), timeout=P.ids.COMMANDS[command]['timeout_ms'] / 1000)
    if code:
        raise OSError('storage.space: %s' % P.status_name(code))
    if len(body) < 12:
        raise ValueError('short storage.space reply')
    version, free = struct.unpack_from('<IQ', body)
    if version != 1:
        raise ValueError('unsupported storage.space reply version %d' % version)
    return free


def content_entries(dev, parent=''):
    remote = '/yaffs2/content' + ('/' + parent if parent else '')
    code, entries = dev.list_dir(remote)
    if code:
        raise OSError('list %s: %s' % (remote, P.status_name(code)))
    names = set()
    for kind, size, raw in entries:
        name = raw.decode('utf-8')
        if not name or name in ('.', '..') or '/' in name or '\0' in name or name in names:
            raise ValueError('invalid or repeated name in %s: %r' % (remote, name))
        names.add(name)
        rel = parent + '/' + name if parent else name
        if kind == 1:
            yield from content_entries(dev, rel)
        else:
            yield rel, kind, size


def read_inventory(dev):
    """Hash ranged reads, checking the listed size. Keep special entries visible without following them."""
    files, special = {}, []
    last_progress = time.monotonic()
    for rel, kind, size in content_entries(dev):
        if kind != 0:
            special.append(rel)
            continue
        full = '/yaffs2/content/' + rel
        digest, offset = hashlib.sha256(), 0
        while True:
            code, block = dev.read_file(full, offset)
            if code:
                raise OSError('read %s at %d: %s' % (full, offset, P.status_name(code)))
            offset += len(block)
            if offset > size or len(block) > P.ids.MAX_READ:
                raise ValueError('read exceeds listed size or range for %s' % full)
            digest.update(block)
            if time.monotonic() - last_progress >= 30:
                print(
                    'Read %d files. Reading %s (%d/%d bytes).' % (len(files), rel, offset, size),
                    file=sys.stderr,
                    flush=True,
                )
                last_progress = time.monotonic()
            if len(block) < P.ids.MAX_READ:
                break
        if offset != size:
            raise ValueError('%s listed %d bytes, read %d' % (full, size, offset))
        files[rel] = (digest.hexdigest(), size)
    return files, sorted(special)


def compare_sets(files, special, official):
    differences, matches = {}, []
    for version, manifest in official.items():
        diff = content_manifest.compare(manifest, files)
        differences[version] = {
            label: diff[key]['paths']
            for label, key in [('missing', 'only_a'), ('extra_files', 'only_b'), ('changed', 'differ')]
        }
        if diff['identical'] and not special:
            matches.append(version)
    unknown = sorted(
        rel
        for rel, (digest, _) in files.items()
        if not any(rel in entry and entry[rel][0] == digest for entry in official.values())
    )
    catalog = files.get('op1_factory.json', (None, None))[0]
    catalog_matches = [
        version
        for version, entry in official.items()
        if 'op1_factory.json' in entry and entry['op1_factory.json'][0] == catalog
    ]
    return dict(
        exact_matches=matches,
        file_count=len(files),
        catalog_sha256=catalog,
        catalog_matches=catalog_matches,
        matches_no_official_file=unknown,
        special_entries=special,
        differences=differences,
    )


def content_status(dev):
    try:
        root = paths.XYF / 'content-manifests'
        official = {p.parent.name: content_manifest.load(p) for p in sorted(root.glob('*/content.sha256'))}
        if not official:
            raise ValueError(
                'no official manifests under %s. Create them with uv run xyf-package content-manifest.' % root
            )
        free = free_bytes(dev)
        print('Storage free: %d bytes. Reading /yaffs2/content.' % free, file=sys.stderr, flush=True)
        files, special = read_inventory(dev)
        report = compare_sets(files, special, official)
        print(json.dumps(dict(free_bytes=free, **report), ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError) as error:
        print('content status failed: %s' % error, file=sys.stderr)
        return 1
