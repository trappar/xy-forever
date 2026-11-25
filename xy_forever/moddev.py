"""`uv run xyf-host mods dev` (#204; the developer package's `xyf dev` wraps it, #284): boot a mod under development on
the OP-XY, alone and once, without touching what is saved. Host only; it uses the device's own commands:

  wait   the OP-XY's serial port appearing: the XY Forever menu (with Start at menu, turning the OP-XY on is all)
  push   the mod and its base as a development set, in a folder of its own (mods/dev-<generation>: it never mixes
         with a saved set, even of the same generation): only changed files are sent; store.txt says development=1,
         so the Mods page and config.set never take it for the saved mods' set; older development sets are removed
  build  mods.apply of the mod alone on the saved base (Mod memory as saved), into mods-a or mods-b (job.status):
         every push of a new version builds again
  boot   menu.converge mode 2: the build's content installed, then it boots once (job.status follows the switch);
         USB goes away as the OP-XY starts it

Every phase is printed with its time, then the total. A development build never mixes with saved mods (whatever kit
generation it comes from), and nothing saved changes: the selection, Mod memory, the default and the fingerprint stay.
The next plain power-on finds the default's content missing and converges back to the saved setup by itself; with
Start at menu the menu opens instead, and its first row (or the next `mods dev`) goes on from there.

This module needs only the connection it is given (connect(timeout) -> (device, port, hello text), as xyf_host's), so
the developer package can reuse it without the rest of xyf-host."""

import argparse
import contextlib
import hashlib
import struct
import time
from pathlib import Path

from . import modconfig, modstore, package_records, package_store, transport
from . import modsend as MS
from . import usb_proto as P
from .startup_ready import StartupNotReady, wait_startup_ready

CONVERGE = P.ids.COMMANDS[P.ids.MENU_CONVERGE]
REPORT, SAVED, ONCE = 0, 1, 2  # menu.converge modes
ENDED, BOOTING = 2, 3  # its states (0 idle, 1 running)
NAME = '%s (development)'  # the build's library name (mods.apply's name: 47 bytes at most)
WAIT_S = 600.0  # how long it waits for the OP-XY to be turned on (--wait)
READY_S = 120.0  # then for its storage (the start's recovery scan; a full yaffs scan can take tens of seconds)
BUSY_S = 60.0  # a request answered busy is sent again this long (A's write window closing after a push or a build)
PLAIN = {  # the device's refusals, in plain words
    'busy': 'the OP-XY is busy (a build, a content change or a saved change runs, or its menu asks to repair content):'
    ' try again when its menu is idle',
    'link': 'XY Forever has lost its storage link: turn the OP-XY off and on again',
    'not_found': 'the build is not in the library',
    'state': 'the build is incomplete (no os.ldr)',
    'unsupported': 'this XY Forever is too old for mods dev: update it (protocol 1.30 or later)',
    'declined': 'the OP-XY did not allow this computer (Ask before USB control)',
}


def plain(code):
    name = P.status_name(code)
    return PLAIN.get(name, name)


# -- what to build --------------------------------------------------------------------------------------
def resolve(package_dir, only=None):
    """(generation folder, the mod's package name) for a mod package folder (its metadata says type=mod; its parent is
    the generation folder modkit split wrote) or a generation folder + only."""
    folder = Path(package_dir)
    try:
        base_metadata = package_records.local_name(folder / 'base') if folder.is_dir() else None
        metadata = package_records.local_name(folder) if folder.is_dir() and not base_metadata else None
    except ValueError as error:
        raise MS.Refused(str(error)) from error
    if base_metadata:
        if not only:
            raise MS.Refused('%s is a generation folder: name the mod with --only ID' % folder)
        gen, mod = folder, only
    elif metadata:
        info = MS.info_values((folder / metadata).read_bytes(), metadata)
        if info.get('type') != 'mod':
            raise MS.Refused('%s/%s: type=%s, not a mod package' % (folder, metadata, info.get('type', '?')))
        gen, mod = folder.parent, folder.name
    elif (folder / 'kit.json').is_file() or folder.suffix == '.json':
        raise MS.Refused(
            '%s: an Open-XY kit; split it first (modkit split), then give the generation '
            'folder with --only ID or one package folder' % folder
        )
    else:
        raise MS.Refused('%s: neither a mod package folder nor a generation folder (base/)' % folder)
    package = mod if (gen / mod).is_dir() else 'ext-' + mod
    if not (gen / package / MS.LIST).is_file():
        raise MS.Refused('no package %r in %s' % (mod, gen))
    return gen, package


# -- the device -----------------------------------------------------------------------------------------
def converge_request(mode, entry=''):
    """menu.converge's payload: version 1, mode, the entry id NUL, the SHA-256 of all of it."""
    body = struct.pack('<II', 1, mode) + entry.encode('ascii') + b'\0'
    return body + hashlib.sha256(body).digest()


def converge(dev, mode, entry=''):
    """(status, {version, state, job, reason} or None) of one menu.converge."""
    code, body = dev.call(P.ids.MENU_CONVERGE, converge_request(mode, entry), timeout=CONVERGE['timeout_ms'] / 1000.0)
    _, fields = P.decode_reply(P.ids.MENU_CONVERGE, struct.pack('<I', code) + body)
    return code, fields


def development_sets(dev):
    """The generation folders on the device whose store.txt marks a development set."""
    code, entries = dev.list_dir(MS.ROOT)
    if code == MS.NOT_FOUND:
        return []
    MS.check(code, 'file.list %s' % MS.ROOT)
    out = []
    for kind, _, raw in entries:
        if kind != 1:
            continue
        name = raw.decode('utf-8', 'replace')
        code, data = dev.read_all('%s/%s/%s' % (MS.ROOT, name, modstore.FILE), limit=1024)
        if not code and modstore.development(data):
            out.append(name)
    return out


def remove_older(dev, keep, out=print):
    """Removes every development set but keep: a development build needs no package once it is built."""
    older = [g for g in development_sets(dev) if g != keep]
    if older:
        with package_store.Session(dev) as session:
            for generation in older:
                session.remove('%s/%s' % (MS.ROOT, generation))
                out('removed the older development set %s' % generation)


def patient(dev, request, timeout=BUSY_S, clock=time.monotonic):
    """request() -> (status, ...), sent again while the device answers busy (paused between), for timeout at most."""
    start = clock()
    while True:
        answer = request()
        if answer[0] != P.ids.S_BUSY or clock() - start > timeout:
            return answer
        transport.pause(dev.t, 0.25)


def ready(dev, text, timeout=READY_S):
    """Waits until the menu's start-up storage work is done (storage.space answers ok, not busy)."""

    def probe(remaining):
        code = dev.call(P.ids.STORAGE_SPACE, struct.pack('<I', 1), timeout=min(remaining, 15.0))[0]
        if code == P.ids.S_BUSY:
            transport.pause(dev.t, 0.25)
        return code

    wait_startup_ready(text, probe, timeout)


def follow(dev, out=print, poll=0.25, sleep=None):
    """Follows the boot once (menu.converge mode 0; job.status of its content switch) until USB goes away: True. The
    menu staying, with why (printed): False."""
    sleep = sleep or (lambda s: transport.pause(dev.t, s))
    shown = None
    while True:
        try:
            code, f = converge(dev, REPORT)
            if code:
                out('boot: %s' % plain(code))
                return False
            if f['state'] == ENDED:
                out('boot: the OP-XY stayed in its menu: %s' % (f['reason'] or 'no reason given'))
                return False
            if f['state'] == BOOTING:
                out('boot: the OP-XY is starting the build')
                return True
            if f['job']:
                code, j = MS.job_status(dev, f['job'])
                if not code and j['phase'] != shown:
                    shown = j['phase']
                    out('boot: installing its content (job %d: %s)' % (f['job'], MS.describe(j)))
        except OSError, TimeoutError:  # USB went away: the menu closed to start it
            out('boot: USB went away: the OP-XY is starting the build')
            return True
        sleep(poll)


# -- one run --------------------------------------------------------------------------------------------
class Phases:
    def __init__(self, out):
        self.out, self.start, self.times = out, time.monotonic(), []

    def run(self, name, fn):
        t = time.monotonic()
        result = fn()
        self.times.append((name, time.monotonic() - t))
        self.out('[%s] %.2f s' % (name, self.times[-1][1]))
        return result

    def total(self):
        self.out('total %.2f s (%s)' % (time.monotonic() - self.start, ', '.join('%s %.2f' % t for t in self.times)))


def build(dev, args, gen, package, folder, out=print):
    """mods.apply of the mod alone from the development set's folder, followed to its end: the library entry, or None
    (said why)."""
    profile = MS.local_set(gen, [package])[0]
    code, saved = patient(dev, lambda: modconfig.get(dev))  # the saved base and Mod memory (read only)
    if code:
        out('build: the saved setup could not be read (%s)' % plain(code))
    base = args.base or (saved or {}).get('base')
    if not base:
        out('build: no stock OP-XY firmware to build on: give its library entry with --base ID')
        return None
    machines, extensions = ([], [package[4:]]) if package.startswith('ext-') else ([package], [])
    expanded = bool(saved and saved['flags'] & modconfig.EXPANDED)
    code, job, missing = patient(
        dev,
        lambda: MS.start(dev, base, profile, folder, machines, extensions, NAME % package, args.unverified, expanded),
    )
    if code:
        if missing and code == MS.NOT_FOUND:
            why = 'missing %s' % MS.MISSING[missing]
        elif P.status_name(code) == 'state':
            why = 'both mod slots are taken (the default and the saved setup)'
        else:
            why = plain(code)
        out('build: refused: %s' % why)
        return None
    f = MS.wait(dev, job, out)
    if f is None:
        return None
    MS.served(dev, out)
    if f['phase'] == MS.DONE:
        return f['detail']
    if f['error'] < len(MS.ERRORS) and MS.ERRORS[f['error']] == 'base' and f['code'] == 18:
        out("build: the mod's base is not one XY Forever knows; if you trust your kit, run again with --unverified")
    else:
        out('build: failed (%s)' % MS.describe(f))
    return None


def dev_once(dev, args, gen, package, out=print):
    """Push, build, boot once. The exit code."""
    ph = Phases(out)
    folder = modstore.DEVELOPMENT_PREFIX + MS.local_set(gen, [package])[1]

    def push():
        remove_older(dev, folder, out)
        summary = MS.push(dev, gen, [package], out=out, development=True)
        MS.served(dev, out)  # A's write window closed
        return summary

    def boot():
        code, _ = patient(dev, lambda: converge(dev, ONCE, entry))
        if code:
            out('boot: refused: %s' % plain(code))
            return False
        out('boot: %s, "%s", once; nothing saved changes' % (entry, NAME % package))
        return follow(dev, out)

    ph.run('push', push)
    entry = ph.run('build', lambda: build(dev, args, gen, package, folder, out))
    booted = bool(entry) and ph.run('boot', boot)
    ph.total()
    if booted:
        out('The next plain start of the OP-XY goes back to the saved setup.')
    return 0 if booted else 1


def add_arguments(sub):
    d = sub.add_parser(
        'dev',
        help='mod developers: push a mod, build it alone on the device and boot it once (nothing saved changes)',
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    d.add_argument('package_dir', type=Path, help="one mod's package folder (or a generation folder with --only ID)")
    d.add_argument('--only', metavar='ID', help='the mod, when PACKAGE_DIR is a generation folder')
    d.add_argument('--base', metavar='ID', help='library entry holding the stock os.ldr (default: the saved base)')
    d.add_argument('--unverified', action='store_true', help="the mod's base is not a known one (you trust it)")
    d.add_argument(
        '--wait', type=float, default=WAIT_S, metavar='S', help='seconds to wait for the OP-XY (default %(default)d)'
    )


def command(args, connect, out=print):
    try:
        gen, package = resolve(args.package_dir, args.only)
        MS.local_set(gen, [package])
    except (MS.Refused, OSError) as error:
        out('mods dev: refused: %s' % error)
        return 2
    t = time.monotonic()
    out('waiting for the OP-XY: turn it on (the XY Forever menu, USB connected)')
    dev, port, text = connect(args.wait)
    hello = P.Hello(text)
    if hello.major != '1' or not hello.answers(P.ids.MENU_CONVERGE):
        out('mods dev: refused: %s' % PLAIN['unsupported'])
        dev.t.close()
        return 2
    try:
        ready(dev, text)
        out('[wait] %.2f s: connected %s: %s' % (time.monotonic() - t, port, text))
        return dev_once(dev, args, gen, package, out)
    except StartupNotReady as error:
        out('mods dev: %s' % error)
        return 1
    except KeyboardInterrupt:
        out('mods dev: stopped')
        return 1
    except MS.Refused as error:
        out('mods dev: %s' % error)
        return 1
    except (OSError, TimeoutError) as error:
        out('mods dev: connection lost (%s); run it again' % error)
        return 1
    finally:
        with contextlib.suppress(OSError):
            dev.t.close()
