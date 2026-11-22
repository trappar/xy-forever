"""Publish or detach an inbox sound pack with one directory rename."""

import json
import secrets
import struct
import time

from . import depth
from . import package_store as store
from . import usb_proto as P
from .census import ENGINE_ACTIONS

DLC = '/yaffs2/content/dlc'
INBOX = store.ROOT + '/inbox'
SIGNED_RECORD = b'xyf-pack.txt'
OP_STATUS, OP_IMPORT, OP_CANCEL, OP_ENABLE, OP_DISABLE, OP_TRACE = range(6)
TRACE_ENTRIES = 32  # target/file_engine.hpp trace_entries; then trace_state_words of A's state
# #195 diagnostic: B's log (target/file_client.hpp log_add): outcomes, flags, the ops it names
B_OUTCOMES = {0: 'answered', 1: 'refused', 2: 'no answer in its bound', 3: 'busy', 4: 'link lost', 15: 'pending'}
B_FLAGS = ((1, 'left out'), (2, 'kept for its owner'), (4, 'answered late'), (8, 'not sent'))
B_OPS = {
    0x08: 'mount',
    0x09: 'unmount',
    0x11: 'sector',
    0x18: 'select/command',
    0x21: 'access',
    0x22: 'stat',
    0x23: 'statfs',
    0x25: 'unlink',
    0x26: 'mkdir',
    0x27: 'rmdir',
    0x29: 'open',
    0x2A: 'read',
    0x2B: 'write',
    0x2C: 'seek',
    0x2D: 'close',
    0x2E: 'opendir',
    0x2F: 'readdir',
    0x30: 'closedir',
    0x31: 'free space',
    0xA0: 'mark',
    0xC0: 'command item',
    0xC1: 'transfer item',
}
SIGNED_OPERATIONS = {'import': OP_IMPORT, 'enable': OP_ENABLE, 'disable': OP_DISABLE}


def configure(sub):
    packs = sub.add_parser('packs', help='manage installed and inbox sound packs')
    actions = packs.add_subparsers(dest='pack_command', required=True)
    actions.add_parser('list', help='list installed pack IDs and vendor metadata')
    for action, help_text in (
        ('import', 'authenticate an inbox .tfw and retain it'),
        ('enable', 'activate a compatible signed pack'),
        ('disable', 'retain a signed pack without activating it'),
    ):
        parser = actions.add_parser(action, help=help_text)
        parser.add_argument('id', help='inbox .tfw filename' if action == 'import' else 'exact pack ID')
        parser.add_argument('--yes', action='store_true', help='apply the change')
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
    if args.pack_command == 'import' and not args.id.lower().endswith('.tfw'):
        print('refused: expected an inbox .tfw filename')
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
    if SIGNED_RECORD in entries:
        raise store.StoreError('signed pack record is reserved')
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
        if kind_at(session, installed, SIGNED_RECORD.decode('ascii')) is not None:
            raise store.StoreError('signed pack: use packs disable to retain it')
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


def signed_request(dev, operation, name='', busy_s=0.0):
    """busy_s: how long a busy device is asked again (an import right after a change waits for the
    write window to settle, about 8 s)."""
    payload = struct.pack('<II', 1, operation) + name.encode('utf-8') + b'\0'
    deadline = time.monotonic() + busy_s
    while True:
        code, body = dev.call(P.ids.PACKS_MANAGE, payload, timeout=60.0)
        if code != P.ids.S_BUSY or time.monotonic() >= deadline:
            break
        time.sleep(0.5)
    if code == P.ids.S_REFUSED and operation == OP_ENABLE:
        raise store.StoreError('compatibility unknown or pack path refused')
    store.check(code, 'signed pack')
    if len(body) < 28 or (len(body) - 28) % 4 or (operation != OP_TRACE and len(body) != 28):
        raise store.StoreError('signed pack reply of %d bytes' % len(body))
    return struct.unpack_from('<7I', body)


def signed_trace(dev):
    """The trace words of the last failed import (packs.manage operation 5)."""
    payload = struct.pack('<II', 1, OP_TRACE) + b'\0'
    code, body = dev.call(P.ids.PACKS_MANAGE, payload, timeout=60.0)
    store.check(code, 'signed pack trace')
    count = struct.unpack_from('<7I', body)[6]
    if len(body) != 28 + 4 * count:
        raise store.StoreError('signed pack trace of %d bytes for %d words' % (len(body), count))
    return struct.unpack_from('<%dI' % count, body, 28)


def failure_site(site, detail):
    """The device's record of where a failed import stopped (#195), as one line."""
    steps = ('hash', 'admit', 'window', 'extract', 'blob setup', 'blobs', 'record', 'publish', 'cleanup', 'close')
    calls = {1: 'open', 2: 'extract', 3: 'write', 4: 'close'}
    step = (site >> 8) & 0xFF
    text = 'step %s' % (steps[step] if step < len(steps) else step)
    if site & 0xFF:
        text += ', %s' % calls.get(site & 0xFF, site & 0xFF)
    text += ', member %d, detail 0x%04x' % (detail >> 16, detail & 0xFFFF)
    flags = [
        name
        for bit, name in ((0x10000, 'source close failed'), (0x20000, 'link lost'), (0x40000, 'cleanup started'))
        if site & bit
    ]
    return text + ('; ' + ', '.join(flags) if flags else '')


def trace_lines(words):
    """#195 diagnostic: the trace words (packs.manage operation 5) as text: B's log, then A's trace."""
    if not words:
        return []
    b_count, a_count, stop = words[0] & 0xFF, words[0] >> 8 & 0xFF, words[0] >> 16 & 0xFF
    b_words = words[1 : 1 + 4 * b_count]
    lines = ["B's last requests to A, oldest first (ms from the first):"]
    lines += ['  ' + line for line in b_log_lines(b_words)]
    if stop:
        lines.append("A's trace: %d pairs, then its request %s" % (a_count, B_OUTCOMES.get(stop, stop)))
    else:
        lines.append("A's trace, oldest first:")
    lines += ['  ' + line for line in a_trace_lines(words[1 + 4 * b_count : 1 + 4 * b_count + 2 * a_count])]
    return lines


def b_log_lines(words):
    """#195 diagnostic: B's own log entries (target/file_client.hpp log_add), one per line."""
    lines = []
    first = words[1] if words else 0
    for head, sent, value, tail in zip(words[0::4], words[1::4], words[2::4], words[3::4]):
        op, outcome, flags, argument = head & 0xFF, head >> 8 & 0xF, head >> 12 & 0xF, head >> 16
        took = tail >> 16
        text = '%+7d ms op 0x%02x %s 0x%04x' % ((sent - first) & 0xFFFFFFFF, op, B_OPS.get(op, '?'), argument)
        if outcome == 0 and op < 0xA0:
            shown = '0x%08x' % value if op in (0x2E, 0x2F) else value - (1 << 32) if value & 0x80000000 else value
            text += ' -> %s errno %d' % (shown, tail & 0xFFFF)
        elif outcome:
            text += ' -> %s' % B_OUTCOMES.get(outcome, outcome)
        if op < 0xA0 or op == 0xC0:
            text += ', %s ms' % ('>65534' if took == 0xFFFF else took)
        named = [name for bit, name in B_FLAGS if flags & bit]
        lines.append(text + ('; ' + ', '.join(named) if named else ''))
    return lines


def a_trace_lines(words):
    """#195 diagnostic: A's trace pairs, one request or state word per line."""
    pairs = list(zip(words[0::2], words[1::2]))
    entries, state = pairs[:TRACE_ENTRIES], pairs[TRACE_ENTRIES:]
    lines = []
    for head, tail in entries:
        if not head and not tail:
            continue
        op, flags = head & 0xFF, head >> 16
        before = 'mounted=%d window=%d/%d engine=%d handles=%d hash=%d' % (
            flags & 1,
            flags >> 1 & 1,
            flags >> 2 & 1,
            flags >> 3 & 3,
            flags >> 5 & 7,
            flags >> 8 & 1,
        )
        if op == 0xF1:
            lines.append('A closed its idle window: result %d; before: %s' % (tail, before))
        elif op == 0xF2:
            lines.append('A left its write entry for another command item: result %d; before: %s' % (tail, before))
        else:
            value = tail & 0xFFFF
            action = head >> 8 & 0xFF
            name = ENGINE_ACTIONS[action - 0x60] if 0x60 <= action < 0x60 + len(ENGINE_ACTIONS) else hex(action)
            shown = '0x%04x' % value if op in (0x2E, 0x2F) else str(value - 0x10000 if value & 0x8000 else value)
            lines.append('op 0x%02x %s -> %s errno %d; before: %s' % (op, name, shown, tail >> 16, before))
    names = ('traced, handles', 'registry begin, end', 'descriptors begin, end', 'posix errno, yaffs error')
    for i, (a, b) in enumerate(state):
        name = names[i] if i < len(names) else 'descriptor %d (provider, handle)' % (i - 1)
        lines.append('A now: %s = 0x%08x, 0x%08x' % (name, a, b))
    return lines


def signed_change(dev, action, pack_id):
    operation = SIGNED_OPERATIONS[action]
    signed_request(dev, operation, pack_id, busy_s=20.0 if operation == OP_IMPORT else 0.0)
    if operation != OP_IMPORT:
        print('%s pack %s' % ('Enabled' if operation == OP_ENABLE else 'Disabled', pack_id))
        return 0
    previous = None
    try:
        while True:
            phase, done, total, error, site, detail, traced = signed_request(dev, OP_STATUS)
            if phase == 3:
                print('Imported %s. The signed pack is retained.' % pack_id)
                return 0
            if phase == 4:
                if site:
                    print('Stopped at %s.' % failure_site(site, detail))
                if traced:
                    print('Trace (#195 diagnostic):')
                    for line in trace_lines(signed_trace(dev)):
                        print('  ' + line)
                store.check(error or P.ids.S_STATE, 'signed pack import')
            if phase not in (1, 2):
                raise store.StoreError('signed pack import stopped unexpectedly')
            percent = done * 100 // max(total, 1)
            mark = (phase, percent // 10)
            if mark != previous:
                print('%s %d%%' % ('Authenticating' if phase == 1 else 'Staging', percent), flush=True)
                previous = mark
            time.sleep(0.25)
    except KeyboardInterrupt:
        signed_request(dev, OP_CANCEL)
        while signed_request(dev, OP_STATUS)[0] in (1, 2):
            time.sleep(0.25)
        print('Signed pack import stopped.')
        return 130


def command(dev, args):
    if args.pack_command == 'list':
        return list_packs(dev)
    action, pack_id = args.pack_command, args.id
    try:
        if action in SIGNED_OPERATIONS:
            return signed_change(dev, action, pack_id)
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
