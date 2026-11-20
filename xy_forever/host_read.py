"""Read a file or list the installed sound packs through existing read-only serial commands."""
import json
from pathlib import Path
import sys

from . import usb_proto as P

DLC = '/yaffs2/content/dlc'


def configure(sub):
    read = sub.add_parser('read')
    read.add_argument('path')
    read.add_argument('--out', type=Path)
    read.add_argument('--all', action='store_true', help='any size (ranged reads; needs the Test 038 service)')
    read.add_argument('--offset', type=int, help='one ranged read (up to READ_RANGE bytes) at this offset')
    packs = sub.add_parser('packs', help='sound packs in /yaffs2/content/dlc')
    packs.add_subparsers(required=True).add_parser('list', help='list installed pack IDs and vendor metadata')


def read(dev, args):
    code, body = (dev.read_all(args.path) if args.all else
                  dev.read_file(args.path, args.offset) if args.offset is not None else dev.read_file(args.path))
    if code:
        print('read failed: %s' % P.status_name(code))
        return code
    if args.out:
        args.out.write_bytes(body)
    else:
        sys.stdout.write(body.decode('utf-8', 'replace'))
    return 0


def list_packs(dev):
    code, children = dev.list_dir(DLC)
    if code == P.ids.S_NOT_FOUND:
        children = []
    elif code:
        print('list %s: %s' % (DLC, P.status_name(code)))
        return code
    rows = []
    for kind, _, raw in children:
        if kind != 1:
            continue
        pack = raw.decode('utf-8')
        if not pack or pack in ('.', '..') or '/' in pack or '\0' in pack:
            print('invalid pack folder in directory listing: %r' % pack)
            return 1
        folder = DLC + '/' + pack
        code, entries = dev.list_dir(folder)
        if code:
            print('list %s: %s' % (folder, P.status_name(code)))
            return code
        if not any(k == 0 and name == b'manifest.json' for k, _, name in entries):
            continue
        path = folder + '/manifest.json'
        code, body = dev.read_all(path, limit=1 << 16)
        if code and code != P.ids.S_TOO_LARGE:
            print('read %s: %s' % (path, P.status_name(code)))
            return code
        row = dict(id=pack)
        if code:
            row['error'] = 'manifest too large'
        else:
            try:
                manifest = json.loads(body)
                if not isinstance(manifest, dict):
                    raise ValueError('expected an object')
                for field in ('name', 'version', 'author', 'description'):
                    if field in manifest:
                        row[field] = manifest[field]
            except (ValueError, RecursionError) as error:
                row['error'] = 'invalid manifest: %s' % error
        rows.append(row)
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    return 0
