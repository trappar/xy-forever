"""xyf: the mod developer's commands, published on PyPI as `xy-forever` (#284; its page: pypi/README.md).

  xyf dev KIT                  the mod in an Open-XY kit (`sdk.py kit --personal --out KIT`): split (Open-XY's
                               core.mjs under Node), then pushed, built alone on the OP-XY and booted once
  xyf dev --watch FOLDER       the same for every new kit in FOLDER (a kit folder inside it, or FOLDER itself)
  xyf logs [--out DIR]         the OP-XY's new crash logs and reports, copied to DIR

The device steps are `xyf-host mods dev`'s (moddev.py: wait, push, build, boot, one timed line each). Every command
first checks the protocol the OP-XY's HELLO names against the range this package speaks (PROTOCOL): a mismatch stops
with one line naming both and the update. Exit codes: 0 done (dev: the mod booted), 1 failed, 2 refused, 130
stopped (Ctrl-C). Needs macOS or Linux (the serial port, transport.py) and, for dev, Node.
"""

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

from xy_forever import usb_proto as P

PROTOCOL = (1, 30)  # the oldest protocol xyf speaks (menu.converge, #204); every later 1.x answers it too
SITE = 'https://xy-forever.trappar.com'
UPDATE_FIRMWARE = 'Update XY Forever at %s (Manage → Device → Update)' % SITE
UPDATE_XYF = 'Update xyf: pip install -U xy-forever (or uvx xy-forever@latest)'
NODE = "Node is missing: install Node 22.12 or newer (https://nodejs.org), as Open-XY's own guide asks"
KIT = 'kit.json'
DONE = 'opxy-builder.html'  # `sdk.py kit` names it last, once every check passed: the kit is complete
POLL_S = 0.5  # --watch: how often it looks for a new kit
LOG_FOLDERS = ('/yaffs2/logs', '/yaffs2/xy-forever/log')  # the OS's crash logs; XY Forever's reports of the last boot
LOG_LIMIT = 1 << 20  # bytes read of one log file
SHOWN = 4096  # bytes of a text log printed inline
SEEN = 'seen.json'  # in the --out folder: {device path: SHA-256} of what was copied before
STOPPED = 130


class Refused(Exception):
    """A command that cannot start: the message says why, in plain words."""


class Incompatible(Exception):
    """The OP-XY speaks a protocol this xyf does not: the message names both and the update."""


def version():
    try:
        from importlib.metadata import version as installed

        return installed('xy-forever')
    except Exception:
        return '?'


def speaks():
    return '%d.%d or a later %d.x' % (PROTOCOL[0], PROTOCOL[1], PROTOCOL[0])


def protocol_problem(text):
    """None when this xyf speaks the protocol the HELLO text names, else the one line to print."""
    proto = P.Hello(text).proto
    try:
        parts = [int(p) for p in proto.split('.')]
    except ValueError:
        parts = [0]
    found = (parts + [0])[:2]
    if found[0] == PROTOCOL[0] and found[1] >= PROTOCOL[1]:
        return None
    line = 'the OP-XY speaks XY Forever protocol %s; xyf needs %s. ' % (proto, speaks())
    return line + (UPDATE_XYF if found[0] > PROTOCOL[0] else UPDATE_FIRMWARE)


def checked_connect(timeout, log=None, waiting=None):
    """device_client.connect, then the protocol check: (device, port, HELLO text) or Incompatible (port closed)."""
    from xy_forever import device_client

    dev, port, text = device_client.connect(timeout, log=log or (lambda line: None), waiting=waiting)
    problem = protocol_problem(text)
    if problem:
        dev.t.close()
        raise Incompatible(problem)
    return dev, port, text


# -- xyf dev --------------------------------------------------------------------------------------------
def kit_file(path):
    """The kit.json of a kit folder (or the file itself)."""
    path = Path(path)
    kit = path if path.is_file() else path / KIT
    if not kit.is_file():
        raise Refused('%s holds no %s: give the folder `sdk.py kit --personal --out FOLDER` wrote' % (path, KIT))
    return kit


def choose(kit, only=None):
    """The id of the mod to try: --only's, else the kit's one personal machine or extension (or its one machine)."""
    machines = [m['id'] for m in kit.get('machines', [])]
    extensions = [e['id'] for e in kit.get('extensions', [])]
    if only:
        if only in machines + extensions:
            return only
        raise Refused('no machine or extension %r in the kit (it holds %s)' % (only, ', '.join(machines + extensions)))
    personal = [m['id'] for m in kit.get('machines', []) if m.get('origin') == 'personal']
    personal += [e['id'] for e in kit.get('extensions', []) if e.get('origin') == 'personal']
    found = personal or machines
    if len(found) == 1:
        return found[0]
    raise Refused(
        'the kit holds %s: name the one to try with --only ID'
        % ('no machine' if not found else '%d mods (%s)' % (len(found), ', '.join(found)))
    )


def open_xy(sdk):
    """The Open-XY checkout whose builder/core.mjs finishes the mod's content."""
    folder = Path(sdk) if sdk else Path.cwd()
    if not (folder / 'builder' / 'core.mjs').is_file():
        raise Refused(
            'no Open-XY checkout at %s (builder/core.mjs): run xyf from your Open-XY checkout or give --sdk DIR'
            % folder
        )
    return folder


def split(kit_path, out, sdk):
    """modkit split of the kit into out: the generation folder."""
    from xy_forever import modkit

    return Path(modkit.split_kit(kit_path, out, sdk)['root'])


def dev_kit(args, kit_dir, out=print, splitter=split, connect=checked_connect):
    """One kit: split, then moddev's push, build and boot. The exit code."""
    from xy_forever import moddev, modkit

    name = 'xyf dev'
    try:
        kit_path = kit_file(kit_dir)
        try:
            kit = json.loads(kit_path.read_text(encoding='utf-8'))
        except ValueError as error:
            raise Refused('%s is not a kit (%s)' % (kit_path, error))
        if not isinstance(kit, dict):
            raise Refused('%s is not a kit' % kit_path)
        mod = choose(kit, args.only)
        sdk = open_xy(args.sdk)
        if not shutil.which('node'):
            raise Refused(NODE)
    except Refused as error:
        out('%s: refused: %s' % (name, error))
        return 2
    with tempfile.TemporaryDirectory(prefix='xyf-dev-') as tmp:
        try:
            generation = splitter(kit_path, tmp, sdk)
        except (modkit.KitError, OSError) as error:
            out('%s: failed: the kit could not be split: %s' % (name, error))
            return 1
        except KeyboardInterrupt:
            out('%s: stopped' % name)
            return STOPPED
        run = SimpleNamespace(
            package_dir=generation,  # with only: moddev finds the machine's or the extension's package
            only=mod,
            base=args.base,
            unverified=args.unverified,
            wait=args.wait,
            verbose=args.verbose,
        )
        try:
            return moddev.command(run, connect, out, name)
        except Incompatible as error:
            out('%s: %s' % (name, error))
            return 2
        except SystemExit as error:  # device_client.connect: no OP-XY menu within --wait
            out('%s: %s' % (name, error.code))
            return 1


def kits(folder):
    """{kit folder: when `sdk.py kit` finished it} for folder and the folders in it."""
    found = {}
    for candidate in [folder, *sorted(p for p in folder.iterdir() if p.is_dir())]:
        done = candidate / DONE
        if done.is_file() and (candidate / KIT).is_file():
            found[candidate] = done.stat().st_mtime_ns
    return found


def watch(args, out=print, sleep=time.sleep, run=dev_kit):
    """dev_kit for each kit that appears or is rebuilt in args.folder after it starts (the newest, when several
    did), until Ctrl-C."""
    folder = Path(args.folder)
    if not folder.is_dir():
        out('xyf dev: refused: %s is not a folder' % folder)
        return 2
    out('xyf dev: watching %s for new kits (Ctrl-C stops)' % folder)
    try:
        seen = kits(folder)
        while True:
            now = kits(folder)
            new = [kit for kit, stamp in now.items() if seen.get(kit) != stamp]
            seen = now
            if new:
                kit = max(new, key=lambda k: now[k])
                out('kit %s' % kit)
                if run(args, kit, out) == STOPPED:
                    return STOPPED
            sleep(POLL_S)
    except KeyboardInterrupt:
        out('xyf dev: stopped')
        return STOPPED


def dev(args, out=print):
    return watch(args, out) if args.watch else dev_kit(args, args.folder, out)


# -- xyf logs -------------------------------------------------------------------------------------------
def device_files(dev, folder, depth=2):
    """[(path, size)] of the files under folder (missing: none), depth folders deep at most."""
    code, entries = dev.list_dir(folder)
    if code:
        return []
    found = []
    for kind, size, raw in entries:
        path = '%s/%s' % (folder, raw.decode('utf-8', 'replace'))
        if kind == 0:  # a file (1 a folder, 2 anything else)
            found.append((path, size))
        elif kind == 1 and depth > 1:
            found += device_files(dev, path, depth - 1)
    return found


def as_text(data):
    """data as text when it reads as text (UTF-8, no control characters but tabs and line ends), else None."""
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError:
        return None
    return text if all(c.isprintable() or c in '\t\r\n' for c in text) else None


def show(out, data, local):
    """A text log inline (indented, its first SHOWN bytes), else nothing."""
    text = as_text(data[:SHOWN]) if data else None
    if text is None:
        return
    lines = text.rstrip('\n').splitlines()
    for line in lines:
        out('  ' + line)
    if len(data) > SHOWN:
        out('  ... (the rest is in %s)' % local)


def copy_new(dev, folder, out=print):
    """Copies each log file whose bytes are not the ones copied before into folder: [device path]."""
    from xy_forever import moddev

    record = folder / SEEN
    try:
        seen = json.loads(record.read_text())
    except (OSError, ValueError):
        seen = {}
    files = [f for root in LOG_FOLDERS for f in device_files(dev, root)]
    new = []
    for path, size in files:
        code, data = dev.read_all(path, limit=LOG_LIMIT)
        whole = not code
        if code and not (P.status_name(code) == 'too_large' and data):
            out('%s: not read (%s)' % (path, moddev.plain(code)))
            continue
        digest = hashlib.sha256(data).hexdigest()
        if seen.get(path) == digest:
            continue
        local = folder / path[len('/yaffs2/') :]
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(data)
        seen[path] = digest
        new.append(path)
        out('%s (%d bytes%s): %s' % (path, size, '' if whole else ', the first %d' % len(data), local))
        show(out, data, local)
    folder.mkdir(parents=True, exist_ok=True)
    record.write_text(json.dumps(seen, indent=1, sort_keys=True) + '\n')
    out('xyf logs: %d new of %d on the OP-XY' % (len(new), len(files)))
    return new


def logs(args, out=print, connect=checked_connect):
    from xy_forever import moddev
    from xy_forever.startup_ready import StartupNotReady

    name = 'xyf logs'
    waited = []

    def waiting():
        waited.append(time.monotonic())
        out(moddev.POWER)

    start = time.monotonic()
    detail = (lambda line: out('  ' + line)) if args.verbose else (lambda line: None)
    try:
        dev, port, text = connect(args.wait, log=detail, waiting=waiting)
    except Incompatible as error:
        out('%s: %s' % (name, error))
        return 2
    except SystemExit as error:
        out('%s: %s' % (name, error.code))
        return 1
    except KeyboardInterrupt:
        out('%s: stopped' % name)
        return STOPPED
    detail('connected %s: %s' % (port, text))
    try:
        moddev.ready(dev, text)
        if waited:
            out('[wait] %.2f s' % (time.monotonic() - start))
        copy_new(dev, Path(args.out), out)
        return 0
    except StartupNotReady as error:
        out('%s: %s' % (name, error))
        return 1
    except (OSError, TimeoutError) as error:
        out('%s: connection lost (%s); run it again' % (name, error))
        return 1
    except KeyboardInterrupt:
        out('%s: stopped' % name)
        return STOPPED
    finally:
        try:
            dev.t.close()
        except OSError:
            pass


# -- the command line ---------------------------------------------------------------------------------
def parser():
    p = argparse.ArgumentParser(
        prog='xyf',
        description="Try your Open-XY mod on an OP-XY running XY Forever: the device steps after Open-XY's build.",
        epilog='Each command waits for the XY Forever menu; when the OP-XY is off or running its OS it says once: '
        '"Power the OP-XY off, then on holding PLAY (let go at the logo)". Exit codes: 0 done (dev: the mod '
        'booted), 1 failed, 2 refused, 130 stopped. More: https://pypi.org/project/xy-forever/',
    )
    p.add_argument('--version', action='version', version='xyf %s (XY Forever protocol %s)' % (version(), speaks()))
    sub = p.add_subparsers(dest='command', required=True, metavar='COMMAND')
    d = sub.add_parser(
        'dev',
        help='push the mod in a kit, build it alone on the OP-XY and boot it once (nothing saved changes)',
        description='Push the mod in an Open-XY kit, build it alone on the OP-XY and boot it once. Lines: '
        '[wait] (only after a power cycle), [push], [build], [boot], each with its time, then total. Nothing saved '
        'changes: the next plain start goes back to your own mods.',
    )
    d.add_argument(
        'folder',
        type=Path,
        help='the kit folder (sdk.py kit --personal --out FOLDER); with --watch, the folder new kits appear in',
    )
    d.add_argument('--watch', action='store_true', help='repeat for every new kit in FOLDER, until Ctrl-C')
    d.add_argument('--only', metavar='ID', help='the machine or extension to try, when the kit holds several')
    d.add_argument('--sdk', type=Path, metavar='DIR', help='your Open-XY checkout (default: the current folder)')
    d.add_argument('--base', metavar='ID', help='the stock firmware entry to build on (default: the saved base)')
    d.add_argument('--unverified', action='store_true', help='build on a base XY Forever does not know (you trust it)')
    d.add_argument('--wait', type=float, default=600.0, metavar='S', help='seconds to wait for the OP-XY (default 600)')
    d.add_argument('--verbose', action='store_true', help='also the connection, every package and the jobs')
    lg = sub.add_parser(
        'logs',
        help="copy the OP-XY's new crash logs and reports from the last boot",
        description="Copy the OP-XY's crash logs (%s) and XY Forever's reports of the last boot (%s) that are new "
        'since the last run into --out; text ones are printed too.' % LOG_FOLDERS,
    )
    lg.add_argument('--out', type=Path, default=Path('xyf-logs'), help='where they go (default: ./xyf-logs)')
    lg.add_argument(
        '--wait', type=float, default=600.0, metavar='S', help='seconds to wait for the OP-XY (default 600)'
    )
    lg.add_argument('--verbose', action='store_true', help='also the connection')
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    if sys.platform == 'win32':
        print('xyf: needs macOS or Linux for now (its USB serial connection)')
        return 2
    return dev(args) if args.command == 'dev' else logs(args)


if __name__ == '__main__':
    sys.exit(main())
