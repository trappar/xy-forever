#!/usr/bin/env python3
"""Set up a private folder for XY Forever development (plain Python 3, no packages).

    python3 scripts/setup.py init [--private DIR]   create the private layout; --private records DIR
                                                   in xyf.local.json (gitignored; XYF_PRIVATE wins)
    python3 scripts/setup.py import-stock DIR      copy the stock 1.2.1 os.ldr + bootloader.ldr from DIR
                                                   (the two files, or a copied /upgrade/ tree)
    python3 scripts/setup.py fixtures              what tests need from the stock pair + public open-xy
    python3 scripts/setup.py doctor                what is set up, what is missing, what will skip

The stock pair comes from your own OP-XY on stock 1.2.1: in the stock dev menu's MTP, copy
/upgrade/firmware/blackfin/bin/os.ldr and bootloader.ldr (or the whole /upgrade/ folder).
Pins: stock.json. Network: fixtures clones public open-xy and fetches its published kit.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import urllib.request

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from xy_forever import paths  # noqa: E402

PINS = json.loads((REPO / 'stock.json').read_text())
PAIR = ('os.ldr', 'bootloader.ldr')
LAYOUT = ('FIRMWARE', 'STOCK_121', 'XYF', 'BUILDS', 'RECOVERY', 'FIXTURES', 'REPORTS', 'ANALYSIS', 'PREVIEWS',
          'SESSIONS', 'MOD_PACKAGES')


class SetupError(Exception):
    pass


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def inside_checkout(path):
    return any((p / '.git').exists() for p in (path, *path.parents))


# init

def write_config(private, config=paths.LOCAL_CONFIG):
    """Record the private folder for this clone (xyf.local.json at the repository root)."""
    private = Path(private).expanduser().resolve()
    if inside_checkout(private):
        raise SetupError('the private folder must be outside every git checkout: %s' % private)
    try:
        values = json.loads(Path(config).read_text())                  # keep other keys (leak_patterns)
    except FileNotFoundError:
        values = {}
    values['private'] = str(private)
    Path(config).write_text(json.dumps(values, indent=2) + '\n')
    return private


def make_layout():
    for name in LAYOUT:
        getattr(paths, name).mkdir(parents=True, exist_ok=True)


def cmd_init(args):
    global paths
    if args.private:
        chosen = Path(args.private).expanduser().resolve()
        env = os.environ.get('XYF_PRIVATE')
        if env and Path(env).expanduser().resolve() != chosen:
            raise SetupError('XYF_PRIVATE=%s overrides --private %s; unset it or use the same folder' % (env, chosen))
        write_config(chosen)
        import importlib
        paths = importlib.reload(paths)
    if inside_checkout(paths.PRIVATE_ROOT):
        raise SetupError('the private folder must be outside every git checkout: %s' % paths.PRIVATE_ROOT)
    make_layout()
    print('private folder: %s (%s)' % (paths.PRIVATE_ROOT, private_source()))
    print('next: python3 scripts/setup.py import-stock DIR   (DIR: os.ldr + bootloader.ldr, or a copied /upgrade/)')


def private_source():
    if os.environ.get('XYF_PRIVATE'):
        return 'XYF_PRIVATE'
    return paths.LOCAL_CONFIG.name if paths.LOCAL_CONFIG.is_file() else 'default ../private'


# import-stock

def find_pair(folder):
    """The os.ldr + bootloader.ldr in folder, or in its upgrade tree (folder = /upgrade/ or its parent)."""
    folder = Path(folder).expanduser()
    for base in (folder, folder / 'firmware/blackfin/bin', folder / 'upgrade/firmware/blackfin/bin'):
        if all((base / n).is_file() for n in PAIR):
            return {n: base / n for n in PAIR}
    raise SetupError('no os.ldr + bootloader.ldr in %s (give the folder holding both, or the copied /upgrade/ '
                     'folder: they are in /upgrade/firmware/blackfin/bin/ on the OP-XY)' % folder)


def identify(name, digest, pins=None):
    """The stock version whose pinned file `name` has this SHA-256, else None."""
    for version, files in (pins or PINS['stock']).items():
        if files.get(name) == digest:
            return version
    return None


def import_stock(folder, dest=None, pins=None):
    """Check the pair against the pins and copy a 1.2.1 pair to dest (paths.STOCK_121); returns dest."""
    dest = Path(dest or paths.STOCK_121)
    found = find_pair(folder)
    versions = {}
    for name, path in found.items():
        version = identify(name, sha256(path), pins)
        if version is None:
            raise SetupError('%s: not a known stock file (SHA-256 %s)' % (path, sha256(path)))
        versions[name] = version
    if versions['bootloader.ldr'] != '1.2.1':
        raise SetupError(
            'these are stock %s files: this OP-XY is on 1.2.5, and XY Forever cannot be installed on it. '
            'XY Forever needs a device still on 1.2.1 (the stock 1.2.1 bootloader, whose dev menu installs it). '
            'The 1.2.5 bootloader has no dev menu, and a device on 1.2.5 cannot be downgraded with '
            'teenage engineering\'s built-in firmware updater.' % ' and '.join(sorted(set(versions.values()))))
    if set(versions.values()) != {'1.2.1'}:
        raise SetupError('these are stock %s files; XY Forever needs the stock 1.2.1 pair (os.ldr and '
                         'bootloader.ldr both 1.2.1).' % ' and '.join(sorted(set(versions.values()))))
    dest.mkdir(parents=True, exist_ok=True)
    for name, path in found.items():
        target = dest / name
        if target.is_file() and sha256(target) == sha256(path):
            continue
        tmp = target.with_suffix('.part')
        shutil.copyfile(path, tmp)
        if sha256(tmp) != (pins or PINS['stock'])['1.2.1'][name]:
            tmp.unlink()
            raise SetupError('copy of %s changed on the way; try again' % path)
        tmp.replace(target)
    return dest


def cmd_import_stock(args):
    dest = import_stock(args.dir)
    print('stock 1.2.1 os.ldr + bootloader.ldr: %s' % dest)
    print('next: python3 scripts/setup.py fixtures')


# fixtures

def run(cmd, **kw):
    env = dict(os.environ, XYF_PRIVATE=str(paths.PRIVATE_ROOT))
    r = subprocess.run(cmd, cwd=kw.pop('cwd', REPO), env=env, capture_output=True, text=True, **kw)
    if r.returncode:
        raise SetupError('%s failed:\n%s' % (' '.join(map(str, cmd[:4])), (r.stdout + r.stderr)[-2000:]))
    return r.stdout


def has_commit(repo, commit):
    return (repo / '.git').exists() and subprocess.run(
        ['git', '-C', str(repo), 'cat-file', '-e', commit + '^{commit}'], capture_output=True).returncode == 0


def stock_present():
    return all((paths.STOCK_121 / n).is_file() and sha256(paths.STOCK_121 / n) == PINS['stock']['1.2.1'][n]
               for n in PAIR)


def fixture_open_xy():
    ox, repo = PINS['open_xy'], paths.OPEN_XY_UPSTREAM
    if not repo.exists():
        print('cloning %s -> %s' % (ox['repository'], repo))
        run(['git', 'clone', '--quiet', ox['repository'], str(repo)])
    if not has_commit(repo, ox['sdk_commit']):
        run(['git', '-C', str(repo), 'fetch', '--quiet', 'origin'])
    if not has_commit(repo, ox['sdk_commit']):
        raise SetupError('%s lacks the pinned open-xy commit %s' % (repo, ox['sdk_commit']))
    print('open-xy upstream at %s: ok' % ox['sdk_commit'][:7])


def git_show(path):
    ox = PINS['open_xy']
    return subprocess.run(['git', '-C', str(paths.OPEN_XY_UPSTREAM), 'show', ox['sdk_commit'] + ':' + path],
                          capture_output=True, check=True).stdout


def fixture_catalog():
    ox = PINS['open_xy']
    if paths.CATALOG_121.is_file() and sha256(paths.CATALOG_121) == ox['catalog_sha256']:
        print('stock catalog op1_factory.json: ok')
        return
    data = git_show(ox['catalog_path'])
    if hashlib.sha256(data).hexdigest() != ox['catalog_sha256']:
        raise SetupError('open-xy %s does not match the pinned stock catalog' % ox['catalog_path'])
    (paths.STOCK_121 / 'op1_factory.json').write_bytes(data)
    print('stock catalog op1_factory.json: from open-xy %s' % ox['sdk_commit'][:7])


def fixture_kit():
    ox, kit = PINS['open_xy'], paths.PUBLISHED_KIT
    if kit.is_file() and sha256(kit) == ox['kit_sha256']:
        print('published kit %s: ok' % ox['kit_version'])
        return True
    print('fetching %s (about 21 MB)' % ox['kit_url'])
    kit.parent.mkdir(parents=True, exist_ok=True)
    tmp = kit.with_suffix('.part')
    request = urllib.request.Request(ox['kit_url'], headers={'User-Agent': 'xy-forever-setup'})
    try:
        with urllib.request.urlopen(request, timeout=120) as r, open(tmp, 'wb') as f:
            shutil.copyfileobj(r, f)
    except OSError as e:
        tmp.unlink(missing_ok=True)
        print('  could not fetch it (%s): the tests that need kit %s and its package set skip'
              % (e, ox['kit_version']))
        return False
    if sha256(tmp) != ox['kit_sha256']:
        try:
            version = json.loads(tmp.read_bytes()).get('version')
        except ValueError:
            version = 'unreadable'
        tmp.unlink()
        print('  %s now serves kit %s, not the pinned %s: the tests that need kit %s and its package set skip'
              % (ox['kit_url'], version, ox['kit_version'], ox['kit_version']))
        return False
    tmp.replace(kit)
    print('published kit %s: ok' % ox['kit_version'])
    return True


def fixture_mods():
    ox = PINS['open_xy']
    if (paths.PUBLISHED_MODS / 'base/info.txt').is_file():
        print('mod package set: ok')
        return
    if not shutil.which('node'):
        print('mod package set: skipped (needs Node for open-xy builder/core.mjs)')
        return
    with tempfile.TemporaryDirectory(dir=paths.SESSIONS, prefix='setup-') as tmp:
        catalog = Path(tmp) / 'extensions.json'
        catalog.write_bytes(git_show('catalog/extensions.json'))
        run([sys.executable, '-m', 'xy_forever.modkit', 'split', str(paths.PUBLISHED_KIT),
             '--sdk', str(paths.OPEN_XY_UPSTREAM), '--sdk-commit', ox['sdk_commit'], '--out', str(paths.MOD_PACKAGES),
             '--sdk-source', '%s@%s' % (ox['repository'], ox['sdk_commit']), '--extension-catalog', str(catalog)])
    if not (paths.PUBLISHED_MODS / 'base/info.txt').is_file():
        raise SetupError('modkit split did not write %s' % paths.PUBLISHED_MODS)
    print('mod package set: %s' % paths.PUBLISHED_MODS)


def toolchain_problem():
    from xy_forever import toolchain
    try:
        toolchain.authenticate()
    except (ValueError, OSError) as e:
        return str(e)
    return None


def fixture_build():
    found = paths.newest_verified_build()
    if found:
        print('verified product build: %s' % found.name)
        return
    problem = toolchain_problem()
    if problem:
        print('verified product build: skipped (toolchain: %s; see doctor)' % problem)
        return
    n = 1
    while (paths.BUILDS / ('xy-forever-product-000-%03d' % n)).exists():
        n += 1
    out = paths.BUILDS / ('xy-forever-product-000-%03d' % n)
    print('building %s (a few minutes)' % out.name)
    run([sys.executable, 'xyf_build.py', '--xyf-product', '--out', str(out)])
    run([sys.executable, 'xyf_build.py', '--verify', '--out', str(out)])
    if paths.newest_verified_build() != out:
        raise SetupError('%s did not verify' % out)
    print('verified product build: %s' % out.name)


def cmd_fixtures(args):
    if not stock_present():
        raise SetupError('the stock 1.2.1 pair is missing from %s: run import-stock first' % paths.STOCK_121)
    make_layout()
    fixture_open_xy()
    fixture_catalog()
    if fixture_kit():
        fixture_mods()
    fixture_build()


# doctor

def toolchain_hint():
    candidate = paths.OPEN_XY_UPSTREAM / 'local/toolchain'
    lines = ['Open-XY\'s pinned toolchain: in an open-xy clone run `python sdk.py toolchain fetch` (the toolchain-1 '
             'release: darwin-arm64, windows-x86_64; linux-x86_64: build it, open-xy docs/build.md "On Linux"); '
             'it lands in local/toolchain. Then link it at %s or set XYF_TOOLCHAIN.' % (REPO.parent / 'tools/blackfin')]
    if candidate.is_dir():
        lines.append('found %s: try XYF_TOOLCHAIN=%s' % (candidate, candidate))
    return lines


def doctor():
    """[(label, ok, detail)] plus the list of what will skip."""
    from xy_forever import sim
    rows, skips = [], []
    rows.append(('private folder', paths.PRIVATE_ROOT.is_dir(), '%s (%s)' % (paths.PRIVATE_ROOT, private_source())))
    problem = toolchain_problem()
    rows.append(('toolchain', problem is None, problem or 'authenticated against toolchains.json'))
    if problem:
        rows += [('', False, line) for line in toolchain_hint()]
    for name in PAIR:
        path = paths.STOCK_121 / name
        version = identify(name, sha256(path)) if path.is_file() else None
        rows.append(('stock ' + name, version == '1.2.1', '1.2.1' if version == '1.2.1' else
                     ('stock %s, not 1.2.1' % version if version else 'unknown file' if path.is_file()
                      else 'missing: run import-stock')))
    ox = PINS['open_xy']
    catalog = paths.CATALOG_121.is_file() and sha256(paths.CATALOG_121) == ox['catalog_sha256']
    upstream = has_commit(paths.OPEN_XY_UPSTREAM, ox['sdk_commit'])
    kit = paths.PUBLISHED_KIT.is_file()
    mods = (paths.PUBLISHED_MODS / 'base/info.txt').is_file()
    build = paths.newest_verified_build()
    rows += [('open-xy clone', upstream, ox['sdk_commit'][:7] if upstream else 'missing: run fixtures'),
             ('stock catalog', catalog, 'ok' if catalog else 'missing: run fixtures'),
             ('published kit', kit, ox['kit_version'] if kit else 'missing: run fixtures'),
             ('mod package set', mods, paths.PUBLISHED_MODS.name if mods else 'missing: run fixtures'),
             ('verified build', bool(build), build.name if build else 'missing: run fixtures')]
    node = shutil.which('node')
    version = subprocess.run([node, '--version'], capture_output=True, text=True).stdout.strip() if node else None
    tsc = (REPO / 'clients/ts/node_modules/.bin/tsc').exists()
    rows.append(('node (clients/ts)', bool(node and tsc), '%s; %s' % (
        version or 'not found (Node 22.18+ or 23.6+)', 'node_modules ok' if tsc else 'run `npm ci` in clients/ts')))
    why = sim.available()
    rows.append(('simulator', why is None, why or 'buildable'))
    leaks = paths.leak_patterns()
    rows.append(('leak patterns', bool(leaks), '%d private regexes for tests/test_no_leaks.py' % len(leaks) if leaks else
                 'none: add your name and emails as regexes to "leak_patterns" in %s' % paths.LOCAL_CONFIG.name))
    if not (paths.DECRYPTED.is_dir() and any(paths.DECRYPTED.iterdir())):
        skips.append('official .tfw packages, update keys, decrypted packages and content manifests: never made '
                      'from the stock pair')
    if not (paths.FIXTURES / 'ble-images.json').is_file():
        skips.append('BLE images (fixtures/ble-images.json)')
    if not (paths.REPORTS.is_dir() and any(paths.REPORTS.iterdir())):
        skips.append('device reports')
    return rows, skips


def cmd_doctor(args):
    rows, skips = doctor()
    for label, ok, detail in rows:
        print('%-4s %-20s %s' % (('ok' if ok else '--') if label else '', label, detail))
    print('tests that will skip:')
    for s in skips or ['none of the known private-only groups']:
        print('     ' + s)
    essential = {'private folder', 'toolchain', 'stock os.ldr', 'stock bootloader.ldr'}
    return 0 if all(ok for label, ok, _ in rows if label in essential) else 1


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='command', required=True)
    i = sub.add_parser('init', help='create the private layout')
    i.add_argument('--private', help='private folder for this clone (recorded in xyf.local.json)')
    s = sub.add_parser('import-stock', help='copy the stock 1.2.1 pair')
    s.add_argument('dir')
    sub.add_parser('fixtures', help='make test inputs from the stock pair and public open-xy')
    sub.add_parser('doctor', help='summary')
    args = p.parse_args(argv)
    try:
        return {'init': cmd_init, 'import-stock': cmd_import_stock, 'fixtures': cmd_fixtures,
                'doctor': cmd_doctor}[args.command](args) or 0
    except SetupError as e:
        print('setup: ' + str(e), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
