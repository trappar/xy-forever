"""Publish or detach an inbox sound pack with one directory rename."""

import json
import secrets
import struct
import time

from . import depth
from . import package_store as store
from . import usb_proto as P

DLC = '/yaffs2/content/dlc'
INBOX = store.ROOT + '/inbox'
RETAINED = store.ROOT + '/packs'  # signed packs kept read-only while not active
RECORDS = store.PACK_RECORDS  # a signed pack's records (RECORDS/<id>/xyf-pack.txt), outside the folder the OS reads
SIGNED_RECORD = b'xyf-pack.txt'
OP_STATUS, OP_IMPORT, OP_CANCEL, OP_ENABLE, OP_DISABLE, OP_TRACE, OP_TIMING = range(7)
# #279: the last import's time on B by bucket (target/usb_service_signed_pack.ipp PackClock), in units of 1024 cycles
# of the 400 MHz clock; then its counts.
TIMING_BUCKETS = (
    'hash',
    'admit',
    'source',
    'aes',
    'inflate',
    'sha',
    'put',
    'member',
    'service',
    'loop',
    'other',
    'finish',
)
TIMING_COUNTS = ('reads', 'puts', 'members', 'bytes')
UNIT_S = 1024 / 400e6
# B's log of its requests to A (target/file_client.hpp log_add): outcomes, flags, the ops it names
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
# A's managed blob requests (target/managed_blob_link.hpp): op 0x21 or 0x2b, argument 0x42 << 8 | the operation
B_BLOB_OPS = {0: 'blob link', 1: 'blob seal', 2: 'blob close', 3: 'blob create', 4: 'blob put'}
SIGNED_OPERATIONS = {'import': OP_IMPORT, 'enable': OP_ENABLE, 'disable': OP_DISABLE}


def configure(sub):
    packs = sub.add_parser('packs', help='manage installed and inbox sound packs')
    actions = packs.add_subparsers(dest='pack_command', required=True)
    actions.add_parser('list', help='list installed pack IDs and vendor metadata')
    for action, help_text in (
        ('import', 'authenticate an inbox .tfw and retain it'),
        ('enable', 'activate a retained signed pack (not tested on this firmware)'),
        ('disable', 'retain a signed pack without activating it'),
    ):
        parser = actions.add_parser(action, help=help_text)
        parser.add_argument('id', help='inbox .tfw filename' if action == 'import' else 'exact pack ID')
        parser.add_argument('--yes', action='store_true', help='apply the change')
    for name in ('install', 'remove'):
        parser = actions.add_parser(name, help=name + ' one plain-folder pack')
        parser.add_argument('id', help='exact pack folder name')
        parser.add_argument('--yes', action='store_true', help='apply the change')
    parser = actions.add_parser('forget', help='delete a retained signed pack and the blobs only it used')
    parser.add_argument('id', help='exact pack ID')
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
        if signed(session, pack_id):
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


def signed(session, pack_id):
    """Whether pack_id has a signed pack's record."""
    return kind_at(session, RECORDS + '/' + pack_id, SIGNED_RECORD.decode('ascii')) == 0


def detach(session, path):
    """Move path into a scratch folder of tmp/ by one rename, then erase it there."""
    folder(session, store.ROOT, 'tmp', optional=True)
    scratch = store.TMP + '/pkg-v1-' + secrets.token_hex(8)
    check_tree(session, path, scratch)
    session.change(P.ids.MAKE_DIR, store.TMP)
    session.rename(path, scratch)
    session.erase(scratch)


def forget(session, pack_id):
    """A retained signed pack deleted, then its records, then every blob nothing else references (the pack's own
    unless another pack or firmware shares them); the number of blobs removed. An active pack is refused."""
    if not valid_name(pack_id):
        raise store.StoreError('invalid pack ID')
    retained = kind_at(session, RETAINED, pack_id) == 1
    records = kind_at(session, RECORDS, pack_id) == 1
    if records and kind_at(session, DLC, pack_id) is not None:
        raise store.StoreError('active signed pack: use packs disable first')
    if not retained and not records:
        raise store.StoreError('no retained signed pack %s' % pack_id)
    if retained:
        detach(session, RETAINED + '/' + pack_id)  # first: records without their pack list nothing
    if records:
        detach(session, RECORDS + '/' + pack_id)
    return session.collect()


def signed_request(dev, operation, name='', busy_s=0.0):
    """busy_s: how long a busy device is asked again (an import right after a change waits for the
    write window to settle, about 8 s)."""
    payload = struct.pack('<II', 1, operation) + name.encode('utf-8') + b'\0'
    code, body = store.unless_busy(dev, lambda: dev.call(P.ids.PACKS_MANAGE, payload, timeout=60.0), busy_s, 0.5)
    if code == P.ids.S_REFUSED and operation == OP_ENABLE:
        raise store.StoreError('pack path refused')
    store.check(code, 'signed pack')
    if len(body) < 28 or (len(body) - 28) % 4 or (operation not in (OP_TRACE, OP_TIMING) and len(body) != 28):
        raise store.StoreError('signed pack reply of %d bytes' % len(body))
    return struct.unpack_from('<7I', body)


def signed_trace(dev):
    """B's log words kept by the last failed import (packs.manage operation 5)."""
    payload = struct.pack('<II', 1, OP_TRACE) + b'\0'
    code, body = dev.call(P.ids.PACKS_MANAGE, payload, timeout=60.0)
    store.check(code, 'signed pack trace')
    count = struct.unpack_from('<7I', body)[6]
    if len(body) != 28 + 16 * count:
        raise store.StoreError('signed pack trace of %d bytes for %d entries' % (len(body), count))
    return struct.unpack_from('<%dI' % (4 * count), body, 28)


def signed_timing(dev):
    """The last import's timing (packs.manage operation 6): {bucket: seconds} and {count: value}."""
    payload = struct.pack('<II', 1, OP_TIMING) + b'\0'
    code, body = dev.call(P.ids.PACKS_MANAGE, payload, timeout=60.0)
    store.check(code, 'signed pack timing')
    words = struct.unpack_from('<%dI' % ((len(body) - 28) // 4), body, 28)
    if not words or words[0] != len(TIMING_BUCKETS) or len(words) != 1 + len(TIMING_BUCKETS) + len(TIMING_COUNTS):
        raise store.StoreError('signed pack timing of %d words' % len(words))
    spent = dict(zip(TIMING_BUCKETS, (units * UNIT_S for units in words[1:])))
    return spent, dict(zip(TIMING_COUNTS, words[1 + len(TIMING_BUCKETS) :]))


def timing_lines(spent, counts):
    """The timing as lines: the phases, then extraction by what B did."""
    phases = ('hash', 'admit', 'finish')
    extraction = sum(seconds for name, seconds in spent.items() if name not in phases)
    return [
        'Time on B: authenticate %.1f s, admit %.1f s, extract %.1f s, finish %.1f s'
        % (spent['hash'], spent['admit'], extraction, spent['finish']),
        'Extract: ' + ', '.join('%s %.1f s' % (name, seconds) for name, seconds in spent.items() if name not in phases),
        'Extract: %(members)d members, %(bytes)d bytes, %(reads)d source reads, %(puts)d pieces put' % counts,
    ]


def print_timing(dev):
    try:
        lines = timing_lines(*signed_timing(dev))
    except (OSError, store.StoreError) as error:  # an older device: no timing
        print('No import timing: %s' % error)
        return
    for line in lines:
        print(line)


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


def b_log_lines(words):
    """B's log of its last requests to A (packs.manage operation 5, 4 words per entry), oldest first, one per line;
    times in ms from the first."""
    lines = []
    first = words[1] if words else 0
    for head, sent, value, tail in zip(words[0::4], words[1::4], words[2::4], words[3::4]):
        op, outcome, flags, argument = head & 0xFF, head >> 8 & 0xF, head >> 12 & 0xF, head >> 16
        took = tail >> 16
        name = B_OPS.get(op, '?')
        if op in (0x21, 0x2B) and argument >> 8 == 0x42:
            name = B_BLOB_OPS.get(argument & 0xFF, name)
        text = '%+7d ms op 0x%02x %s 0x%04x' % ((sent - first) & 0xFFFFFFFF, op, name, argument)
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


def signed_change(dev, action, pack_id):
    operation = SIGNED_OPERATIONS[action]
    signed_request(dev, operation, pack_id, busy_s=20.0 if operation == OP_IMPORT else 0.0)
    if operation != OP_IMPORT:
        print(
            'Enabled pack %s (not tested on this firmware)' % pack_id
            if operation == OP_ENABLE
            else 'Disabled pack ' + pack_id
        )
        return 0
    previous = None
    started = time.monotonic()
    try:
        while True:
            phase, done, total, error, site, detail, traced = signed_request(dev, OP_STATUS)
            if phase == 3:
                print('Imported %s in %.0f s. The signed pack is retained.' % (pack_id, time.monotonic() - started))
                print_timing(dev)
                return 0
            if phase == 4:
                print_timing(dev)
                if site:
                    print('Stopped at %s.' % failure_site(site, detail))
                if traced:
                    print("B's last requests to A (ms from the first):")
                    for line in b_log_lines(signed_trace(dev)):
                        print('  ' + line)
                store.check(error or P.ids.S_STATE, 'signed pack import')
            if phase not in (1, 2):
                raise store.StoreError('signed pack import stopped unexpectedly')
            percent = done * 100 // max(total, 1)
            mark = (phase, percent // 10)
            if mark != previous:
                what = 'Authenticating' if phase == 1 else 'Staging'
                print('%s %d%% (%.0f s)' % (what, percent, time.monotonic() - started), flush=True)
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
            if action == 'forget':
                removed = forget(session, pack_id)
                print('Forgot pack %s; removed %d unshared blobs.' % (pack_id, removed))
                return 0
            change(session, action, pack_id)
    except (OSError, ValueError, RecursionError) as error:
        print('pack %s failed: %s' % (action, error))
        return 1
    print('%s pack %s' % ('Installed' if action == 'install' else 'Removed', pack_id))
    return 0


def pack_rows(dev, root, state, rows, records):
    """Append a row per pack folder of root (one with a manifest.json); 0, or the status that stopped it. records: the
    IDs with a signed pack's record."""
    code, children = store.unless_busy(dev, lambda: dev.list_dir(root))  # a toggle's window still settling
    if code == P.ids.S_NOT_FOUND:
        children = []
    elif code:
        print('list %s: %s' % (root, P.status_name(code)))
        return code
    for kind, _, raw in children:
        if kind != 1:
            continue
        pack = raw.decode('utf-8')
        if not pack or pack in ('.', '..') or '/' in pack or '\0' in pack:
            print('invalid pack folder in directory listing: %r' % pack)
            return 1
        folder = root + '/' + pack
        code, entries = store.unless_busy(dev, lambda: dev.list_dir(folder))
        if code:
            print('list %s: %s' % (folder, P.status_name(code)))
            return code
        names = {name for k, _, name in entries if k == 0}
        if b'manifest.json' not in names:
            continue
        path = folder + '/manifest.json'
        code, body = store.unless_busy(dev, lambda: dev.read_all(path, limit=1 << 16))
        if code and code != P.ids.S_TOO_LARGE:
            print('read %s: %s' % (path, P.status_name(code)))
            return code
        row = dict(id=pack, state=state)
        if raw in records:
            row['signed'] = True
            row['firmware'] = 'not tested on this firmware'
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
    return 0


def list_packs(dev):
    """Installed packs (content/dlc/, state active) and retained signed packs (xy-forever/packs/, state retained),
    with their manifest's metadata; a signed pack (its record in pack-records/) is trusted on any official base but not
    tested on this firmware, and says so."""
    rows = []
    code, folders = store.unless_busy(dev, lambda: dev.list_dir(RECORDS))
    if code == P.ids.S_NOT_FOUND:
        folders = []
    elif code:
        print('list %s: %s' % (RECORDS, P.status_name(code)))
        return code
    records = set()
    for kind, _, raw in folders:
        if kind != 1:
            continue
        folder = RECORDS + '/' + raw.decode('utf-8')
        code, entries = store.unless_busy(dev, lambda: dev.list_dir(folder))
        if code:
            print('list %s: %s' % (folder, P.status_name(code)))
            return code
        if (0, SIGNED_RECORD) in {(k, name) for k, _, name in entries}:
            records.add(raw)
    for root, state in ((DLC, 'active'), (RETAINED, 'retained')):
        code = pack_rows(dev, root, state, rows, records)
        if code:
            return code
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    return 0
