"""xyf: the mod developer's commands, published on PyPI as `xy-forever` (#284; its page: pypi/README.md).

  xyf stock-os [OUT]           the stock OS 1.2.1 os.ldr from the OP-XY's own library, for `sdk.py build --stock-os`
  xyf dev BUILD                a full firmware image (Open-XY's `sdk.py build --out BUILD`, or any other): checked as
                               the device checks library images, pushed as library entry xyf-dev with its content
                               (if any), booted once (dev_entry.py)
  xyf dev --watch FOLDER       the same for every new build in FOLDER (a build folder inside it, or FOLDER itself)
  xyf logs [--out DIR]         the OP-XY's new crash logs and reports, copied to DIR

Every command that talks to the OP-XY waits for its XY Forever menu (saying once how to get there), then checks the
protocol its HELLO names against the range that command needs (NEEDS): a mismatch stops with one line naming both
and the update. Exit codes: 0 done (dev: the build booted), 1 failed, 2 refused, 130 stopped (Ctrl-C).
"""

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

from xy_forever import usb_proto as P

MAJOR = 1  # every later 1.x answers what an earlier one did
# The oldest protocol each command needs: dev, menu.converge mode 2 making the base's content set first (1.31, #284);
# logs and stock-os, file.list (1.5) and file.read.
NEEDS = {'dev': (1, 31), 'logs': (1, 5), 'stock-os': (1, 5)}
SITE = 'https://xy-forever.trappar.com'
UPDATE_FIRMWARE = 'Update XY Forever at %s (Manage > Device > Update)' % SITE  # ASCII: a Windows pipe is cp1252
UPDATE_XYF = 'Update xyf: pip install -U xy-forever (or uvx xy-forever@latest)'
DONE = 'verification.json'  # `sdk.py build` writes it last, once the build passed its checks
POLL_S = 0.5  # --watch: how often it looks for a new build
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


def speaks(need=None):
    need = need or NEEDS['dev']
    return '%d.%d or a later %d.x' % (need[0], need[1], MAJOR)


def protocol_problem(text, need=None):
    """None when the protocol the HELLO text names answers a command needing need (default: dev's), else the line."""
    need = need or NEEDS['dev']
    proto = P.Hello(text).proto
    try:
        parts = [int(p) for p in proto.split('.')]
    except ValueError:
        parts = [0]
    found = tuple((parts + [0])[:2])
    if found[0] == MAJOR and found >= need:
        return None
    line = 'the OP-XY speaks XY Forever protocol %s; xyf needs %s. ' % (proto, speaks(need))
    return line + (UPDATE_XYF if found[0] > MAJOR else UPDATE_FIRMWARE)


def checked_connect(timeout, log=None, waiting=None, need=None):
    """device_client.connect, then the protocol check: (device, port, HELLO text) or Incompatible (port closed)."""
    from xy_forever import device_client

    dev, port, text = device_client.connect(timeout, log=log or (lambda line: None), waiting=waiting)
    problem = protocol_problem(text, need)
    if problem:
        dev.t.close()
        raise Incompatible(problem)
    return dev, port, text


def connector(command):
    """checked_connect with that command's need."""
    return lambda timeout, log=None, waiting=None: checked_connect(timeout, log, waiting, NEEDS[command])


def session(name, args, work, out=print, connect=None):
    """Connects (the power line once, when the OP-XY is not at its menu), waits for the menu's storage, says [wait]
    when it waited, then work(dev, detail) -> the exit code. Every way out ends in one line."""
    from xy_forever import dev_entry, moddev
    from xy_forever.startup_ready import StartupNotReady

    waited = []

    def waiting():
        waited.append(time.monotonic())
        out(moddev.POWER)

    start = time.monotonic()
    detail = (lambda line: out('  ' + line)) if getattr(args, 'verbose', False) else (lambda line: None)
    try:
        dev, port, text = connect(args.wait, log=detail, waiting=waiting)
    except Incompatible as error:
        out('%s: %s' % (name, error))
        return 2
    except SystemExit as error:  # device_client.connect: no XY Forever menu within --wait
        if waited:
            out(
                '%s: the XY Forever menu did not answer within %g s (--wait): power the OP-XY on holding PLAY, then '
                'run it again' % (name, args.wait)
            )
        else:
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
        return work(dev, detail)
    except (Refused, dev_entry.Refused) as error:
        out('%s: refused: %s' % (name, error))
        return 2
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


# -- xyf stock-os ---------------------------------------------------------------------------------------
def private_root():
    """Open-XY's folder for private files (its tooling/device.py private_root): firmware stays outside checkouts."""
    if sys.platform == 'darwin':
        return Path.home() / 'Library/Application Support/OPXY-SDK/private'
    return Path(os.environ.get('LOCALAPPDATA') or Path.home() / '.local/share') / 'OPXY-SDK/private'


def default_stock():
    """Where `xyf stock-os` writes the stock os.ldr and `xyf dev` reads it: Open-XY's stock-1.2.1/os.ldr."""
    return private_root() / 'stock-1.2.1' / 'os.ldr'


def stock_target(out):
    target = Path(out) if out else default_stock()
    return target / 'os.ldr' if target.is_dir() else target


def stock_os(args, out=print, connect=None):
    """Copies the library entry whose os.ldr is stock OS 1.2.1 (by SHA-256) to the target, checked; never prints it."""
    from xy_forever import dev_entry

    name, target = 'xyf stock-os', stock_target(args.out)
    try:
        dev_entry.read_stock(target)
        out('%s: %s is stock OS 1.2.1 already' % (name, target))
        return 0
    except dev_entry.Refused:
        pass

    def work(dev, detail):
        start = time.monotonic()
        entry = dev_entry.stock_entries(dev)[0]
        code, data = dev.read_all(
            '%s/%s/os.ldr' % (dev_entry.package_store.LIBRARY, entry), limit=dev_entry.STOCK_BYTES
        )
        if code or len(data) != dev_entry.STOCK_BYTES or hashlib.sha256(data).hexdigest() != dev_entry.STOCK_SHA256:
            out('%s: failed: %s/os.ldr did not read back as stock OS 1.2.1; run it again' % (name, entry))
            return 1
        target.parent.mkdir(parents=True, exist_ok=True)
        part = target.with_name(target.name + '.part')
        part.write_bytes(data)
        os.replace(part, target)
        out(
            '%s: %s (%d bytes, SHA-256 checked, from library entry %s) in %.2f s'
            % (name, target, len(data), entry, time.monotonic() - start)
        )
        return 0

    return session(name, args, work, out, connect or connector('stock-os'))


# -- xyf dev --------------------------------------------------------------------------------------------
def dev_build(args, folder, out=print, connect=None):
    """One build: checked here as the device checks a library image, then pushed and booted once. The exit code."""
    from xy_forever import dev_entry

    try:
        image, content = dev_entry.read_build(folder)
        dev_entry.check_image(image)
        kind = dev_entry.which(image, args.stock_os or default_stock(), args.sdk or Path.cwd())
        files = dev_entry.entry_files(image, content, kind)
    except dev_entry.Refused as error:
        out('xyf dev: refused: %s' % error)
        return 2

    def work(dev, detail):
        first = dev_entry.preflight(dev)
        return dev_entry.run(dev, files, out, detail, kind, content_first=first)

    return session('xyf dev', args, work, out, connect or connector('dev'))


def builds(folder):
    """{build folder: when `sdk.py build` finished it} for folder and the folders in it."""
    found = {}
    for candidate in [folder, *sorted(p for p in folder.iterdir() if p.is_dir())]:
        image, done = candidate / 'candidate' / 'os.ldr', candidate / DONE
        if image.is_file() and done.is_file():
            found[candidate] = (image.stat().st_mtime_ns, done.stat().st_mtime_ns)
    return found


def watch(args, out=print, sleep=time.sleep, run=dev_build):
    """run for each build that appears or is rebuilt in args.folder after it starts (the newest, when several did),
    until Ctrl-C."""
    folder = Path(args.folder)
    if not folder.is_dir():
        out('xyf dev: refused: %s is not a folder' % folder)
        return 2
    out('xyf dev: watching %s for new builds (Ctrl-C stops)' % folder)
    try:
        seen = builds(folder)
        while True:
            now = builds(folder)
            new = [build for build, stamp in now.items() if seen.get(build) != stamp]
            seen = now
            if new:
                build = max(new, key=lambda b: now[b])
                out('build %s' % build)
                if run(args, build, out) == STOPPED:
                    return STOPPED
            sleep(POLL_S)
    except KeyboardInterrupt:
        out('xyf dev: stopped')
        return STOPPED


def dev(args, out=print):
    return watch(args, out) if args.watch else dev_build(args, args.folder, out)


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


def logs(args, out=print, connect=None):
    def work(dev, detail):
        copy_new(dev, Path(args.out), out)
        return 0

    return session('xyf logs', args, work, out, connect or connector('logs'))


# -- the command line ---------------------------------------------------------------------------------
def parser():
    p = argparse.ArgumentParser(
        prog='xyf',
        description='Try your Open-XY build on an OP-XY running XY Forever: it boots once, your saved setup stays.',
        epilog='Each command that talks to the OP-XY waits for the XY Forever menu; when the OP-XY is off or running '
        'its OS it says once: "Power the OP-XY off, then on holding PLAY (let go at the logo)". Exit codes: 0 done '
        '(dev: the build booted), 1 failed, 2 refused, 130 stopped. More: https://pypi.org/project/xy-forever/',
    )
    p.add_argument(
        '--version',
        action='version',
        version='xyf %s (XY Forever protocol %s; logs and stock-os: %s)' % (version(), speaks(), speaks(NEEDS['logs'])),
    )
    sub = p.add_subparsers(dest='command', required=True, metavar='COMMAND')
    s = sub.add_parser(
        'stock-os',
        help="copy the stock OS 1.2.1 os.ldr from the OP-XY's library, for sdk.py build --stock-os",
        description="Copy the stock OS 1.2.1 os.ldr from the OP-XY's own library (the entry whose SHA-256 is OS "
        "1.2.1's), checked, to OUT (default: %s, where xyf dev looks for it)." % default_stock(),
    )
    s.add_argument('out', nargs='?', type=Path, help='the file or folder to write it to')
    d = sub.add_parser(
        'dev',
        help="push Open-XY's build to the OP-XY and boot it once (nothing saved changes)",
        description='Push the firmware `sdk.py build --out BUILD` made to the OP-XY as library entry xyf-dev, with '
        'its content, and boot it once. Lines: [wait] (only after a power cycle), [push], [boot], each with its '
        'time, then total. The next plain start goes back to your saved setup.',
    )
    d.add_argument(
        'folder',
        type=Path,
        help='the build folder (sdk.py build --out BUILD; it holds candidate/os.ldr); with --watch, the folder new '
        'builds appear in',
    )
    d.add_argument('--watch', action='store_true', help='repeat for every new build in FOLDER, until Ctrl-C')
    d.add_argument(
        '--sdk', type=Path, metavar='DIR', help='your Open-XY checkout, for its profile (default: the current folder)'
    )
    d.add_argument(
        '--stock-os', type=Path, metavar='FILE', help='the stock OS 1.2.1 os.ldr (default: where stock-os writes it)'
    )
    lg = sub.add_parser(
        'logs',
        help="copy the OP-XY's new crash logs and reports from the last boot",
        description="Copy the OP-XY's crash logs (%s) and XY Forever's reports of the last boot (%s) that are new "
        'since the last run into --out; text ones are printed too.' % LOG_FOLDERS,
    )
    lg.add_argument('--out', type=Path, default=Path('xyf-logs'), help='where they go (default: ./xyf-logs)')
    for command in (s, d, lg):
        command.add_argument(
            '--wait', type=float, default=600.0, metavar='S', help='seconds to wait for the OP-XY (default 600)'
        )
        command.add_argument('--verbose', action='store_true', help='also the connection and each step')
    return p


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):  # a path the console's code page lacks: replaced, never a traceback
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(errors='replace')
    args = parser().parse_args(argv)
    return {'stock-os': stock_os, 'dev': dev, 'logs': logs}[args.command](args)


if __name__ == '__main__':
    sys.exit(main())
