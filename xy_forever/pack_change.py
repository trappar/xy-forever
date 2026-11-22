"""Publish or detach an inbox sound pack with one directory rename."""

import json
import secrets

from . import depth
from . import package_store as store
from . import usb_proto as P

DLC = '/yaffs2/content/dlc'
INBOX = store.ROOT + '/inbox'


def configure(sub):
    packs = sub.add_parser('packs', help='manage installed and inbox sound packs')
    actions = packs.add_subparsers(dest='pack_command', required=True)
    actions.add_parser('list', help='list installed pack IDs and vendor metadata')
    for name in ('install', 'remove'):
        parser = actions.add_parser(name, help=name + ' one plain-folder pack')
        parser.add_argument('id', help='exact pack folder name')
        parser.add_argument('--yes', action='store_true', help='apply the change')


def plan(args):
    if args.pack_command == 'list':
        return None
    if not valid_name(args.id):
        print('refused: invalid pack folder ID')
        return 2
    print('Plan: %s pack %s.' % (args.pack_command, args.id))
    if not args.yes:
        print('Dry run. Use --yes to apply.')
        return 0
    return None


def valid_name(name):
    return store.package_name(name) and not name.lower().endswith('.cyacd') and name.lower() != 'ble.bin'


def kind_at(session, parent, name):
    for kind, _, raw in session.entries(parent):
        if raw == name.encode('utf-8'):
            return kind
    return None


def folder(session, parent, name, optional=False):
    kind = kind_at(session, parent, name)
    if kind is None and optional:
        return False
    if kind != 1:
        raise store.StoreError('expected a directory: %s/%s' % (parent, name))
    return True


def check_tree(session, source, target):
    """Look at directory entries, not followed links. Preflight the full destination before a change."""
    for kind, _, raw in session.entries(source):
        name = raw.decode('utf-8')
        if kind not in (0, 1) or not valid_name(name):
            raise store.StoreError('unsupported pack entry: %r' % name)
        child = source + '/' + name
        dest = target + '/' + name
        if max(len(child), len(dest)) >= 240 or depth.refusal(dest, folder=kind == 1):
            raise store.StoreError('pack path exceeds device limits: %s' % dest)
        if kind == 1:
            check_tree(session, child, dest)


class ManifestObject(dict):
    def __init__(self, pairs):
        super().__init__(pairs)
        self.folder_count = sum(key == 'folder' for key, _ in pairs)
        self.native_package = any(key == 'schema' and value == 'xy-forever.package' for key, value in pairs)


def nesting(value):
    if isinstance(value, dict):
        value = value.values()
    elif not isinstance(value, list):
        return 0
    return 1 + max((nesting(child) for child in value), default=0)


def reject_constant(value):
    raise ValueError('invalid JSON constant: ' + value)


def manifest(session, source, pack_id):
    entries = {raw: kind for kind, _, raw in session.entries(source)}
    if entries.get(b'manifest.json') != 0 or b'info.txt' in entries or b'os.ldr' in entries:
        raise store.StoreError('expected an unambiguous sound pack with a regular manifest.json')
    code, data = session.dev.read_all(source + '/manifest.json', limit=4097)
    store.check(code, 'read pack manifest')
    if len(data) > 4096:
        raise store.StoreError('pack manifest exceeds 4096 bytes')
    value = json.loads(data.decode('utf-8'), object_pairs_hook=ManifestObject, parse_constant=reject_constant)
    if nesting(value) > 32:
        raise store.StoreError('pack manifest exceeds 32 nesting levels')
    if not isinstance(value, ManifestObject) or value.folder_count > 1 or value.get('folder', pack_id) != pack_id:
        raise store.StoreError('manifest folder does not match pack ID %s' % pack_id)
    if value.native_package:
        raise store.StoreError('native package manifests cannot be installed as sound packs')


def change(session, action, pack_id):
    if not valid_name(pack_id) or action not in ('install', 'remove'):
        raise store.StoreError('invalid pack ID or action')
    folder(session, '/yaffs2', 'xy-forever')
    folder(session, '/yaffs2', 'content')
    have_dlc = folder(session, '/yaffs2/content', 'dlc', optional=True)
    installed = DLC + '/' + pack_id
    if action == 'install':
        folder(session, store.ROOT, 'inbox')
        folder(session, INBOX, pack_id)
        if have_dlc and kind_at(session, DLC, pack_id) is not None:
            raise store.StoreError('pack ID already occupied: %s' % pack_id)
        source = INBOX + '/' + pack_id
        manifest(session, source, pack_id)
        check_tree(session, source, installed)
        if not have_dlc:
            session.change(P.ids.MAKE_DIR, DLC)
        session.rename(source, installed)
    else:
        folder(session, DLC, pack_id)
        manifest(session, installed, pack_id)
        folder(session, store.ROOT, 'tmp', optional=True)
        scratch = store.TMP + '/pkg-v1-' + secrets.token_hex(8)
        check_tree(session, installed, scratch)
        session.change(P.ids.MAKE_DIR, store.TMP)
        session.rename(installed, scratch)
        try:
            session.erase(scratch)
        except OSError as error:
            print('Pack detached; cleanup pending: %s' % error)


def command(dev, args):
    if args.pack_command == 'list':
        return list_packs(dev)
    action, pack_id = args.pack_command, args.id
    try:
        with store.Session(dev, sweep=False) as session:
            change(session, action, pack_id)
    except (OSError, ValueError, RecursionError) as error:
        print('pack %s failed: %s' % (action, error))
        return 1
    print('%s pack %s' % ('Installed' if action == 'install' else 'Removed', pack_id))
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
