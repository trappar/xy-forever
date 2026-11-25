"""The saved inputs over USB (#26): `uv run xyf-host mods status | set`, through config.get and config.set (protocol 1.24).

They change mods the way the device's Mods page does: config.set sends the selection and Mod memory, the device reads
its package list again, checks that the selection can be built and saves it (or refuses, saying why), as if the
toggles were flipped there. Nothing is built then: the next boot of XY Forever converges to what is saved (#197).
`mods set` with no mods saves none. Safe mode (Boot without mods) is a recovery action on the device only: config.set
carries it, and a set here sends it back as saved, never changing it. The developer commands (`mods apply`,
`mods dev`, `set-default`, `boot`) change what boots behind the saved inputs, once: the next plain power-on returns
to them. `mods status` says when such a build is in use: `mods dev` booted it once with its own content (#204)."""

import hashlib
import struct
import time

from . import transport
from . import usb_proto as P
from .device_client import SLOTS, current_state

COMMANDS = ('status', 'set')
EXPANDED, SAFE, UNVERIFIED = 1, 2, 4  # config.get / config.set flags (SAFE: kept as saved)
SAVING = ('saved', 'waiting for a quiet moment to be written', 'the last write failed: set it again')
READ_S = 60.0  # s: the device reads its package list for a set


def get(dev):
    """(status, fields or None): config.get, mods as a list."""
    code, body = dev.call(
        P.ids.CONFIG_GET, struct.pack('<I', 1), timeout=P.ids.COMMANDS[P.ids.CONFIG_GET]['timeout_ms'] / 1000.0
    )
    _, f = P.decode_reply(P.ids.CONFIG_GET, struct.pack('<I', code) + body)
    if code or f is None:
        return code or P.ids.S_BAD_REQUEST, None
    f['mods'] = f['mods'].split(',') if f['mods'] else []
    return code, f


def request(mods, flags):
    body = struct.pack('<II', 1, flags) + ','.join(mods).encode('ascii') + b'\0'
    return body + hashlib.sha256(body).digest()


def set_inputs(dev, mods, flags, sleep=None, clock=time.monotonic, timeout=READ_S):
    """(status, reason) of config.set, sent again while the device reads its package list (busy). sleep: the
    pause between tries (default: the device's own, transport.pause)."""
    sleep = sleep or (lambda s: transport.pause(dev.t, s))
    start = clock()
    while True:
        code, body = dev.call(
            P.ids.CONFIG_SET, request(mods, flags), timeout=P.ids.COMMANDS[P.ids.CONFIG_SET]['timeout_ms'] / 1000.0
        )
        if code != P.ids.S_BUSY or clock() - start > timeout:
            return code, body[4:].decode('ascii', 'replace') if len(body) > 4 else ''
        sleep(0.2)


def written(dev, sleep=None, clock=time.monotonic, timeout=30.0):
    """config.get once the set is written (saving 0) or its write failed (2); None when neither came in time."""
    sleep = sleep or (lambda s: transport.pause(dev.t, s))
    start = clock()
    while clock() - start <= timeout:
        code, f = get(dev)
        if code == 0 and f['saving'] != 1:
            return f
        sleep(0.2)
    return None


def describe(f):
    lines = [
        'saved: %s'
        % (
            '%d mod%s on %s (%s)'
            % (f['count'], '' if f['count'] == 1 else 's', f['base'] or '?', f['generation'] or '?')
            if f['record']
            else 'none (no mods: %s boots)' % (f['base'] or 'the default')
        )
    ]
    if f['mods']:
        lines.append('mods: %s' % ', '.join(f['mods']))
    lines.append(
        'Mod memory: %s%s'
        % ('4 MiB' if f['flags'] & EXPANDED else '2 MiB', '; unknown base allowed' if f['flags'] & UNVERIFIED else '')
    )
    lines.append('write: %s' % (SAVING[f['saving']] if f['saving'] < len(SAVING) else f['saving']))
    return lines


CONTENT_STATE = '/yaffs2/xy-forever/content-state.txt'


def booted_once(dev):
    """#204: the library entry whose mod content is installed while another entry is the default (`mods dev` booted it
    once; the next plain power-on converges back), or None."""
    code, head = dev.read_file(CONTENT_STATE, 0, 256)
    fields = dict(line.split('=', 1) for line in head.decode('ascii', 'replace').splitlines() if '=' in line)
    if code or fields.get('state') != 'installed' or not fields.get('entry'):
        return None
    slots = {}
    for path in SLOTS:
        code, body = dev.read_file(path)
        slots[path] = body if code == 0 else None
    best = current_state(slots)
    return fields['entry'] if best and best[2].get('default') != fields['entry'] else None


def add_arguments(sub):
    sub.add_parser('status', help='the saved inputs: mods on, Mod memory (config.get)')
    s = sub.add_parser('set', help='save these mods on, as the Mods page does; built at the next boot (config.set)')
    s.add_argument('mods', nargs='*', metavar='MOD', help='package folder names (none: no mods)')
    s.add_argument('--ext', nargs='+', default=[], metavar='ID', help='extension ids (without ext-)')
    s.add_argument('--memory', choices=('standard', 'expanded'), help='Mod memory (default: as saved)')
    s.add_argument('--unverified', action='store_true', help='allow a base that is not a known one')


def command(dev, args, out=print):
    """`mods status | set` on a connected device: the exit code (a status, 0 when saved)."""
    name = 'mods %s' % args.mods_command
    code, f = get(dev)
    if code:
        out('%s: %s' % (name, P.status_name(code)))
        return code
    if args.mods_command == 'status':
        out('\n'.join(describe(f)))
        once = booted_once(dev)
        if once:
            out('development build in use: %s (booted once; the next plain start goes back to the saved setup)' % once)
        return 0
    flags = f['flags'] & (EXPANDED | SAFE | UNVERIFIED)  # safe mode as saved: only the device changes it
    mods = list(args.mods) + ['ext-' + e for e in args.ext]
    if args.memory:
        flags = (flags & ~EXPANDED) | (EXPANDED if args.memory == 'expanded' else 0)
    flags |= UNVERIFIED if args.unverified else 0
    code, reason = set_inputs(dev, mods, flags)
    if code:
        out('%s: %s%s' % (name, P.status_name(code), ': ' + reason if reason else ''))
        return code
    f = written(dev)
    if f is None or f['saving']:
        out('%s: accepted, but %s' % (name, 'not written yet' if f is None else SAVING[2]))
        return 1
    out('%s: saved; the next boot of XY Forever builds and installs it as needed' % name)
    out('\n'.join('  ' + line for line in describe(f)))
    return 0
