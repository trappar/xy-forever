"""`xyf_host.py mods dev`: an edit-build-boot loop for people who write Open-XY mods, without the
dev menu. Host tool only; it uses the existing commands:

  push   modsend.push(by_hash=True): only the files whose SHA-256 differs from the device's files.sha256 line
  apply  mods.apply + job.status: the device builds the selection (the mod plus --with mods) into mods-a/-b
  set    set-default (the content switch installs the entry's content first, then the default)
  boot   library.boot (--boot only; the device then leaves XY Forever)

Every phase is printed with its time, then the total. --watch repeats on a change of any file in the
packages used (mtime poll, no dependency). The way back: the default before the first dev run is kept in a
small state file under the host's cache dir (never on the device); `mods dev --restore` sets it again.

The saved configuration (/yaffs2/xy-forever/config.txt) names the default the menu expects. When it
is present a dev run changes the default behind it, and the menu's Continue line then ends in "(to apply)"
(or "(rebuild)"); mods dev refuses unless --ignore-config, and says what the menu will show. It does not
rewrite config.txt (that record is the menu's own: Save in the menu writes it again)."""
import json
import os
from pathlib import Path
import sys
import time

from . import modsend as MS
from . import usb_proto as P

CONFIG = '/yaffs2/xy-forever/config.txt'
FIRMWARE = '/yaffs2/xy-forever/firmware'
STATE_NAME = 'moddev-state.json'


def cache_dir():
    """$XYF_CACHE, else the platform's user cache folder + xy-forever."""
    if os.environ.get('XYF_CACHE'):
        return Path(os.environ['XYF_CACHE']).expanduser()
    if sys.platform == 'darwin':
        return Path.home() / 'Library/Caches/xy-forever'
    return Path(os.environ.get('XDG_CACHE_HOME') or Path.home() / '.cache') / 'xy-forever'


def state_path():
    return cache_dir() / STATE_NAME


def load_state():
    try:
        return json.loads(state_path().read_text())
    except (OSError, ValueError):
        return {}


def save_state(state):
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.part')
    tmp.write_text(json.dumps(state, indent=1, sort_keys=True) + '\n')
    tmp.replace(path)


# -- what to build --------------------------------------------------------------------------------------
def resolve(package_dir, only=None, with_=()):
    """(generation folder, [package names: the mod first, then --with]) for a mod package folder (its info.txt
    says type=mod; its parent is the generation folder modkit split wrote) or a generation folder + only."""
    folder = Path(package_dir)
    if (folder / 'base' / MS.INFO).is_file():
        if not only:
            raise MS.Refused('%s is a generation folder: name the mod with --only ID' % folder)
        gen, mod = folder, only
    elif (folder / MS.INFO).is_file():
        info = MS.info_values((folder / MS.INFO).read_bytes())
        if info.get('type') != 'mod':
            raise MS.Refused('%s/info.txt: type=%s, not a mod package' % (folder, info.get('type', '?')))
        gen, mod = folder.parent, folder.name
    elif (folder / 'kit.json').is_file() or folder.suffix == '.json':
        raise MS.Refused('%s: an Open-XY kit; split it first (modkit split), then give the generation '
                         'folder with --only ID or one package folder' % folder)
    else:
        raise MS.Refused('%s: neither a mod package folder (info.txt) nor a generation folder (base/)' % folder)
    names = []
    for name in [mod] + list(with_):
        package = name if (gen / name).is_dir() else 'ext-' + name
        if not (gen / package / MS.LIST).is_file():
            raise MS.Refused('no package %r in %s' % (name, gen))
        if package not in names:
            names.append(package)
    return gen, names


def selection(names):
    """(machines, extension ids) for mods.apply from package names (ext-<id> is an extension)."""
    return ([n for n in names if not n.startswith('ext-')], [n[4:] for n in names if n.startswith('ext-')])


def snapshot(gen, names):
    """{path: (mtime_ns, size)} of every file in base/ and the given packages: what --watch polls."""
    out = {}
    for name in ['base'] + list(names):
        for p in (gen / name).rglob('*'):
            if p.is_file():
                st = p.stat()
                out[str(p)] = (st.st_mtime_ns, st.st_size)
    return out


# -- the device's records ---------------------------------------------------------------------------------
def read_text(dev, path):
    code, data = dev.read_all(path, limit=1 << 16)
    if code == MS.NOT_FOUND:
        return None
    MS.check(code, 'file.read %s' % path)
    return data.decode('utf-8', 'replace')


def config(dev):
    """config.txt as a dict (mods: list), or None when absent (no record: the menu flags nothing)."""
    text = read_text(dev, CONFIG)
    if text is None:
        return None
    f = dict(l.split('=', 1) for l in text.splitlines() if '=' in l)
    f['mods'] = [m for m in f.get('mods', '').split(',') if m]
    return f


def menu_after(cfg, default, library):
    """The ending of the menu's Continue line once default is the default (target/xyf_mods_io.cpp
    menu_state): '' when the record agrees, else ' (to apply)' or ' (rebuild)'."""
    if cfg is None:
        return ''
    implied = cfg.get('slot', '') if cfg['mods'] else cfg.get('base', '')
    if implied and implied == default:
        return ''
    return ' (rebuild)' if cfg['mods'] and cfg.get('slot', '') not in library else ' (to apply)'


def describe_config(cfg):
    return 'base %s, %s%s' % (cfg.get('base', '?'), ('mods on: %s' % ','.join(cfg['mods'])) if cfg['mods']
                              else 'no mods on', (', built into %s' % cfg['slot']) if cfg.get('slot') else '')


def library(dev):
    code, entries = dev.list_dir(FIRMWARE)
    return set() if code else {name.decode('utf-8', 'replace') for kind, _, name in entries if kind == 1}


def entry_stamp(dev, entry):
    text = read_text(dev, '%s/%s/info.txt' % (FIRMWARE, entry))
    return MS.info_values(text.encode()).get('sha256', '') if text else ''


def current_default(dev, slots, current_state):
    found = {}
    for path in slots:
        code, body = dev.read_file(path)
        found[path] = body if code == 0 else None
    best = current_state(found)
    return best[2].get('default') if best else None


def set_default(dev, entry, slots, current_state, out=print):
    """set-default with the content switch (as xyf_host.py set-default --yes-write); True when verified."""
    code, _ = dev.set_default(entry)
    if code == P.ids.S_STARTED:
        f = MS.wait(dev, 0, out)
        if f is None or f['phase'] != MS.DONE:
            return False
        MS.served(dev, out)
        code = 0
    if code:
        out('set-default %s: %s' % (entry, P.status_name(code)))
        return False
    got = current_default(dev, slots, current_state)
    if got != entry:
        out('set-default %s: NOT verified (the state slots name %s)' % (entry, got))
        return False
    return True


# -- one loop ---------------------------------------------------------------------------------------------
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
        self.out('total %.2f s (%s)' % (time.monotonic() - self.start,
                                        ', '.join('%s %.2f' % t for t in self.times)))


def target_key(port):
    return str(port)


def dev_once(dev, port, args, gen, names, slots, current_state, out=print):
    """One push-apply-set(-boot) pass. Returns (exit code, summary dict)."""
    ph = Phases(out)
    profile, generation, _ = MS.local_set(gen, names)
    cfg = ph.run('check', lambda: config(dev))
    if cfg is not None and not args.ignore_config:
        out('mods dev: refused: the device has a saved configuration (config.txt: %s).' % describe_config(cfg))
        out('  A dev build becomes the default behind it, and the menu\'s Continue line would end in "(to apply)"')
        out('  until Save in the menu or `mods dev --restore`. Re-run with --ignore-config to accept that.')
        return 2, {}
    state = load_state()
    key = target_key(port)
    if key not in state:
        previous = current_default(dev, slots, current_state)
        state[key] = dict(default=previous, stamp=entry_stamp(dev, previous) if previous else '',
                          saved=time.strftime('%Y-%m-%d %H:%M:%S'))
        save_state(state)
        out('previous default %s remembered in %s (mods dev --restore sets it again)' % (previous, state_path()))
        if previous and previous.startswith('mods-'):
            out('  note: %s is a mods slot; a later dev build may replace it (--restore then sets the slot, '
                'not the old build)' % previous)
    summary = ph.run('push', lambda: MS.push(dev, gen, names, out=out, by_hash=True))
    machines, exts = selection(names)
    base = args.base or (cfg or {}).get('base') or state[key].get('base')
    if not base:
        out('mods dev: name the library entry holding the stock os.ldr with --base ID')
        return 2, summary

    def build():
        code, job, missing = MS.start(dev, base, profile, generation, machines, exts, args.name, args.unverified)
        if code:
            out('apply: %s%s' % (P.status_name(code), ' (missing %s)' % MS.MISSING[missing] if missing else ''))
            return None
        f = MS.wait(dev, job, out)
        if f is None:
            return None
        MS.served(dev, out)
        return f['detail'] if f['phase'] == MS.DONE else None
    entry = ph.run('apply', build)
    if not entry:
        ph.total()
        return 1, summary
    state[key]['base'] = base
    save_state(state)
    if not ph.run('set-default', lambda: set_default(dev, entry, slots, current_state, out)):
        ph.total()
        return 1, summary
    out('default: %s (%s, content installed)' % (entry, ' + '.join(names)))
    if cfg is not None:
        out('menu: the Continue line now ends in "%s" (config.txt kept: %s)' % (
            menu_after(cfg, entry, library(dev)).strip() or 'nothing', describe_config(cfg)))
    if args.boot:
        code = ph.run('boot', lambda: dev.library_boot(entry))
        out('boot %s: %s' % (entry, P.status_name(code)))
        if code:
            ph.total()
            return code, summary
    ph.total()
    summary['entry'] = entry
    return 0, summary


def restore(dev, port, slots, current_state, out=print):
    state = load_state()
    key = target_key(port)
    saved = state.get(key)
    if not saved or not saved.get('default'):
        out('mods dev --restore: nothing remembered for %s in %s' % (key, state_path()))
        return 1
    entry = saved['default']
    if saved.get('stamp') and entry_stamp(dev, entry) != saved['stamp']:
        out('note: %s changed since it was remembered (a dev build replaced it); setting it anyway' % entry)
    t = time.monotonic()
    if not set_default(dev, entry, slots, current_state, out):
        return 1
    del state[key]
    save_state(state)
    out('restored: default %s (%.2f s); %s forgets it' % (entry, time.monotonic() - t, state_path()))
    cfg = config(dev)
    if cfg is not None:
        out('menu: the Continue line ends in "%s" (config.txt: %s)' % (
            menu_after(cfg, entry, library(dev)).strip() or 'nothing', describe_config(cfg)))
    return 0


def watch(dev, port, args, gen, names, slots, current_state, out=print, poll=0.5, sleep=time.sleep, rounds=None):
    """Runs a pass, then again whenever a file of the packages changes. rounds: passes to run (tests)."""
    done = 0
    while True:
        code, _ = dev_once(dev, port, args, gen, names, slots, current_state, out)
        done += 1
        if code == 2 or (rounds is not None and done >= rounds):
            return code
        seen = snapshot(gen, names)
        out('watching %s (%s) for changes; Ctrl-C stops' % (gen, ', '.join(names)))
        while snapshot(gen, names) == seen:
            sleep(poll)
        sleep(poll)                                        # let the build finish writing files.sha256


def add_arguments(sub):
    d = sub.add_parser('dev', help='mod developers: push what changed, build the mod (+ --with) on the device, '
                       'make it the default, --boot it; --watch repeats; --restore puts the old default back',
                       description=__doc__, formatter_class=__import__('argparse').RawDescriptionHelpFormatter)
    d.add_argument('package_dir', type=Path, nargs='?', help="one mod's package folder (or a generation folder "
                   'with --only ID)')
    d.add_argument('--only', metavar='ID', help='the mod, when PACKAGE_DIR is a generation folder')
    d.add_argument('--with', dest='with_', nargs='+', default=[], metavar='ID', help='more mods in the selection')
    d.add_argument('--base', metavar='ID', help='library entry holding the stock os.ldr (default: config.txt base=, '
                   'else the last dev run\'s)')
    d.add_argument('--name', default=MS.DEFAULT_NAME, help='the entry name (default %(default)r)')
    d.add_argument('--unverified', action='store_true', help='the base is not a known one (you confirmed it)')
    d.add_argument('--boot', action='store_true', help='boot the result (the device leaves XY Forever)')
    d.add_argument('--watch', action='store_true', help='repeat on every file change (not with --boot)')
    d.add_argument('--ignore-config', action='store_true', help='go ahead although config.txt exists (the menu '
                   'then shows "(to apply)")')
    d.add_argument('--restore', action='store_true', help='set the default from before the first dev run again')


def command(args, connect, slots, current_state, out=print):
    if not args.restore:
        if args.package_dir is None:
            out('mods dev: give a mod package folder (or --restore)')
            return 2
        if args.boot and args.watch:
            out('mods dev: refused: --boot leaves XY Forever, so --watch cannot repeat; use one of them')
            return 2
        try:
            gen, names = resolve(args.package_dir, args.only, args.with_)
            MS.local_set(gen, names)
        except (MS.Refused, OSError) as error:
            out('mods dev: refused: %s' % error)
            return 2
    dev, port, text = connect(args.timeout)
    out('connected %s: %s' % (port, text))
    if P.Hello(text).major != '1':
        out('refused: the device speaks protocol %s; this tool speaks 1.x' % P.Hello(text).proto)
        return 2
    try:
        if args.restore:
            return restore(dev, port, slots, current_state, out)
        if args.watch:
            return watch(dev, port, args, gen, names, slots, current_state, out)
        return dev_once(dev, port, args, gen, names, slots, current_state, out)[0]
    except KeyboardInterrupt:
        out('mods dev: stopped')
        return 0
    except MS.Refused as error:
        out('mods dev: %s' % error)
        return 1
    except (OSError, TimeoutError) as error:
        out('mods dev: connection lost (%s); run it again' % error)
        return 1
    finally:
        with __import__('contextlib').suppress(OSError):
            dev.t.close()
