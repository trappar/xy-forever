#!/usr/bin/env python3
"""Set up a private folder for XY Forever development (plain Python 3, no packages).

    python3 scripts/setup.py init [--private DIR]   create the private layout; --private records DIR
                                                   in xyf.local.json (gitignored; XYF_PRIVATE wins)
    python3 scripts/setup.py import-stock DIR      copy the stock 1.2.1 os.ldr + bootloader.ldr from DIR
                                                   (the two files, or a copied /upgrade/ tree), and the
                                                   stock catalog op1_factory.json if beside them or in DIR/content/
    python3 scripts/setup.py fixtures              what tests need from the stock pair + public open-xy
    python3 scripts/setup.py doctor                what is set up, what is missing, what will skip
    python3 scripts/setup.py bootstrap [--workspace DIR] [--dry-run] [--toolchain-archive ZIP]
                                                   all of the above that needs no OP-XY, plus the toolchain
                                                   (open-xy's toolchain-1 release, or ZIP already downloaded),
                                                   docs/status/README.md and npm ci; safe to rerun

The stock pair comes from your own OP-XY on stock 1.2.1: in the stock dev menu's MTP, copy
/upgrade/firmware/blackfin/bin/os.ldr and bootloader.ldr (or the whole /upgrade/ folder), and
/content/op1_factory.json (fixtures takes it from public open-xy only when no stock copy is present).
Pins: stock.json. Network: fixtures clones public open-xy and fetches its published kit; bootstrap
downloads the toolchain archive.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from xy_forever import paths  # noqa: E402

PINS = json.loads((REPO / 'stock.json').read_text())
PAIR = ('os.ldr', 'bootloader.ldr')
CATALOG = 'op1_factory.json'                                  # the device's /content/op1_factory.json
LAYOUT = ('FIRMWARE', 'STOCK_121', 'XYF', 'BUILDS', 'RECOVERY', 'FIXTURES', 'REPORTS', 'ANALYSIS', 'PREVIEWS',
          'SESSIONS', 'MOD_PACKAGES')


MIN_PYTHON = (3, 9)                 # XY Forever's own scripts and tests (and bootstrap)


class SetupError(Exception):
    pass


def python_problem(version=None):
    """None, or why this interpreter is too old for XY Forever (3.9+)."""
    v = tuple((version or sys.version_info)[:3])
    if v[:2] >= MIN_PYTHON:
        return None
    return ('Python %d.%d.%d is too old: XY Forever needs 3.9+. Run a newer one by name, e.g. '
            '`python3.12 scripts/setup.py doctor` (macOS: `brew install python@3.12`, python.org, mise or pyenv)' % v)


def require_python(version=None):
    problem = python_problem(version)
    if problem:
        raise SetupError(problem)


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


def ensure_leak_patterns(config=paths.LOCAL_CONFIG):
    """Give the local config a "leak_patterns" list (empty) for the developer to fill; keep other keys."""
    try:
        values = json.loads(Path(config).read_text())
    except FileNotFoundError:
        values = {}
    if 'leak_patterns' not in values:
        values['leak_patterns'] = []
        Path(config).write_text(json.dumps(values, indent=2) + '\n')


def make_layout():
    for name in LAYOUT:
        getattr(paths, name).mkdir(parents=True, exist_ok=True)


def cmd_init(args):
    global paths
    require_python()
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
    ensure_leak_patterns(paths.LOCAL_CONFIG)
    print('private folder: %s (%s)' % (paths.PRIVATE_ROOT, private_source()))
    if not paths.leak_patterns():
        print('next: add your name, emails and host name as regexes to "leak_patterns" in %s '
              '(case-sensitive; "(?i)" first matches any case)' % paths.LOCAL_CONFIG.name)
    print('next: python3 scripts/setup.py import-stock DIR   (DIR: os.ldr + bootloader.ldr, or a copied /upgrade/)')


def private_source():
    if os.environ.get('XYF_PRIVATE'):
        return 'XYF_PRIVATE'
    try:
        configured = json.loads(paths.LOCAL_CONFIG.read_text()).get('private')
    except FileNotFoundError:
        configured = None
    return paths.LOCAL_CONFIG.name if configured else 'default ../private'


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
    import_catalog(folder, found['os.ldr'].parent, dest)
    return dest


def import_catalog(folder, pair_dir, dest):
    """Copy the device's /content/op1_factory.json (beside the pair, or in a copied content/ folder) to dest if
    it is the pinned stock 1.2.1 catalog; a changed copy (Open-XY mods rewrite it) is reported and ignored."""
    folder, pinned = Path(folder).expanduser(), PINS['open_xy']['catalog_sha256']
    for source in dict.fromkeys((pair_dir / CATALOG, folder / CATALOG, folder / 'content' / CATALOG)):
        if not source.is_file():
            continue
        digest = sha256(source)
        target = dest / CATALOG
        if digest != pinned:
            print('stock catalog %s: ignored, not the stock 1.2.1 file (SHA-256 %s; Open-XY mods rewrite it); '
                  'fixtures downloads it from open-xy' % (source, digest))
            continue
        if not (target.is_file() and sha256(target) == pinned):
            tmp = target.with_suffix('.part')
            shutil.copyfile(source, tmp)
            if sha256(tmp) != pinned:
                tmp.unlink()
                print('stock catalog %s: ignored, the copy changed on the way' % source)
                continue
            tmp.replace(target)
        print('stock catalog %s: from your device copy %s' % (CATALOG, source))
        return True
    return False


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
    """Keep a valid stock catalog already present (import-stock's device copy); else take it from open-xy."""
    ox = PINS['open_xy']
    if paths.CATALOG_121.is_file() and sha256(paths.CATALOG_121) == ox['catalog_sha256']:
        print('stock catalog %s: ok, the copy in %s (no download)' % (CATALOG, paths.CATALOG_121.parent))
        return
    data = git_show(ox['catalog_path'])
    if hashlib.sha256(data).hexdigest() != ox['catalog_sha256']:
        raise SetupError('open-xy %s does not match the pinned stock catalog' % ox['catalog_path'])
    (paths.STOCK_121 / CATALOG).write_bytes(data)
    print('stock catalog %s: from open-xy %s (no stock device copy; import-stock takes /content/%s)'
          % (CATALOG, ox['sdk_commit'][:7], CATALOG))


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


def toolchain_problem(directory=None):
    """None, or why the toolchain (XYF_TOOLCHAIN, ../tools/blackfin, or directory) fails its pins."""
    from xy_forever import toolchain
    try:
        toolchain.authenticate(directory)
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
    print('building and verifying %s (under a minute)' % out.name)
    run([sys.executable, 'xyf_build.py', '--xyf-product', '--out', str(out)])
    run([sys.executable, 'xyf_build.py', '--verify', '--out', str(out)])
    if paths.newest_verified_build() != out:
        raise SetupError('%s did not verify' % out)
    print('verified product build: %s' % out.name)


def cmd_fixtures(args):
    require_python()
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
    return ['`python3 scripts/setup.py bootstrap` downloads open-xy\'s toolchain-1 release into %s (darwin-arm64, '
            'windows-x86_64); linux-x86_64: build it (open-xy docs/build.md "On Linux"), then put it there or set '
            'XYF_TOOLCHAIN.' % (REPO.parent / TOOLCHAIN_DIR)]


def doctor():
    """[(label, ok, detail)] plus the list of what will skip."""
    from xy_forever import sim
    rows, skips = [], []
    problem = python_problem()
    rows.append(('python', problem is None, problem or '%d.%d.%d' % tuple(sys.version_info[:3])))
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
                 'none: add your name and emails as regexes to "leak_patterns" in %s (case-sensitive; "(?i)" first '
                 'matches any case)' % paths.LOCAL_CONFIG.name))
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
    essential = {'python', 'private folder', 'toolchain', 'stock os.ldr', 'stock bootloader.ldr'}
    return 0 if all(ok for label, ok, _ in rows if label in essential) else 1


# bootstrap

TOOLCHAIN_DIR = 'tools/blackfin'        # <workspace>/tools/blackfin: open-xy's toolchain-1 release, unpacked
STATUS_INDEX = 'docs/status/README.md'  # the local, gitignored status folder's index (from docs/STATUS.template.md)


def copy_into(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


def move_into(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    source.rename(target)
NPM_DIRS = ('clients/ts', 'web')         # web uses clients/ts: in this order
WORKSPACE_GUIDE = """# XY Forever workspace

This folder is an XY Forever development workspace, set up by `%(repo)s/scripts/setup.py bootstrap`.

| Path | What |
|---|---|
| `%(repo)s/` | the XY Forever repository (git). **Read `%(repo)s/AGENTS.md` first and follow it: all its rules apply.** |
| `%(private)s/` | stock firmware, keys, builds, device reports: local only, never committed |
| `tools/` | the Blackfin toolchain (`tools/blackfin`): local only, never committed |

"Continue development" works from here: treat `%(repo)s/` as the repository for every command (run
them there, as its docs show), and keep everything else in this folder out of git.
"""


def host_name():
    """This host as toolchains.json names it (as xy_forever/toolchain.py does)."""
    machine = platform.machine().lower()
    return platform.system().lower() + '-' + {'aarch64': 'arm64', 'amd64': 'x86_64'}.get(machine, machine)


def download(url, path):
    """url into path (stdlib only; GitHub wants a User-Agent)."""
    request = urllib.request.Request(url, headers={'User-Agent': 'xy-forever-setup'})
    with urllib.request.urlopen(request, timeout=120) as response, Path(path).open('wb') as handle:
        shutil.copyfileobj(response, handle, 1 << 20)


def unpack(archive, folder, destination):
    """folder/* of the zip archive into destination, every entry inside it, executable bits kept (zipfile drops
    them: they are in the high 16 bits of external_attr)."""
    with zipfile.ZipFile(str(archive)) as files:
        for info in files.infolist():
            parts = info.filename.split('/')
            if parts[0] != folder or '..' in parts or '\\' in info.filename or info.filename.startswith('/'):
                raise SetupError('unexpected entry in the toolchain archive: %s' % info.filename)
            if info.is_dir() or len([p for p in parts if p]) == 1:
                continue
            target = destination.joinpath(*parts[1:])
            target.parent.mkdir(parents=True, exist_ok=True)
            with files.open(info) as source, target.open('wb') as handle:
                shutil.copyfileobj(source, handle, 1 << 20)
            if (info.external_attr >> 16) & 0o111:
                target.chmod(0o755)


def install_toolchain(record, destination, archive=None):
    """Download record's archive (or use archive), check its SHA-256, unpack it next to destination, check every
    file against toolchains.json, then move it into place. Nothing is left behind on a failure."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.blackfin-', dir=str(destination.parent)) as tmp:
        if archive is None:
            archive = Path(tmp) / 'archive.zip'
            try:
                download(record['url'], archive)
            except (urllib.error.URLError, OSError) as e:
                note = (' (macOS: run "Install Certificates.command" in the Python folder in Applications)'
                        if 'CERTIFICATE_VERIFY_FAILED' in str(e) else '')
                raise SetupError('could not download %s (%s)%s. Download it in a browser, then run '
                                 '`python3 scripts/setup.py bootstrap --toolchain-archive <the zip>`'
                                 % (record['url'], e, note))
        got = sha256(archive)
        if got != record['sha256']:
            raise SetupError('toolchain archive hash mismatch: %s is %s, the pin (stock.json) is %s: not the '
                             'toolchain-1 release for this host' % (archive, got, record['sha256']))
        staged = Path(tmp) / 'blackfin'
        try:
            unpack(archive, record['folder'], staged)
        except zipfile.BadZipFile as e:
            raise SetupError('%s is not a readable zip (%s)' % (archive, e))
        problem = toolchain_problem(staged)
        if problem:
            raise SetupError('the unpacked toolchain fails its pins (toolchains.json): %s' % problem)
        os.replace(str(staged), str(destination))


def fixtures_missing():
    """What `setup.py fixtures` would still make (the doctor rows it fills)."""
    ox = PINS['open_xy']
    return [name for name, ok in (
        ('open-xy clone', has_commit(paths.OPEN_XY_UPSTREAM, ox['sdk_commit'])),
        ('stock catalog', paths.CATALOG_121.is_file() and sha256(paths.CATALOG_121) == ox['catalog_sha256']),
        ('published kit', paths.PUBLISHED_KIT.is_file()),
        ('mod package set', (paths.PUBLISHED_MODS / 'base/info.txt').is_file()),
        ('verified build', bool(paths.newest_verified_build()) or toolchain_problem() is not None)) if not ok]


class Bootstrap:
    """`setup.py bootstrap`: every step looks first and acts only on what is missing. Never deletes anything."""

    def __init__(self, workspace=None, repo=REPO, config=None, dry_run=False, environ=os.environ,
                 toolchain_archive=None):
        self.repo = Path(repo).resolve()
        self.workspace = Path(workspace or self.repo.parent).expanduser().resolve()
        self.config = Path(config) if config else self.repo / paths.LOCAL_CONFIG.name
        self.dry_run, self.environ, self.actions = dry_run, environ, []
        self.private_root = None
        self.toolchain_archive = Path(toolchain_archive).expanduser().resolve() if toolchain_archive else None

    def do(self, what, action, *args, **kw):
        self.actions.append(what)
        print('  %s %s' % ('would' if self.dry_run else '->', what), flush=True)
        if not self.dry_run:
            action(*args, **kw)

    def settings(self):
        try:
            return json.loads(self.config.read_text())
        except FileNotFoundError:
            return {}

    def run(self):
        require_python()
        print('bootstrap: workspace %s%s' % (self.workspace, ' (dry run: nothing changes)' if self.dry_run else ''))
        for step in (self.private, self.local_files, self.workspace_guide, self.toolchain, self.npm, self.fixtures):
            step()
        if not self.actions:
            print('nothing to do: the workspace is already set up; nothing changed')
        elif self.dry_run:
            print('dry run: the %d actions above would run; nothing changed' % len(self.actions))
        else:
            print('done: %d actions' % len(self.actions))
        print('\ndoctor:')
        cmd_doctor(None)
        self.next_steps()
        return 0

    def private(self):
        env, configured = self.environ.get('XYF_PRIVATE'), self.settings().get('private')
        if env:
            root, source = Path(env).expanduser().resolve(), 'XYF_PRIVATE'
        elif configured:
            root, source = Path(configured).expanduser().resolve(), self.config.name
        else:
            root, source = self.workspace / 'private', 'default ../private'
        if inside_checkout(root):
            raise SetupError('the private folder must be outside every git checkout: %s' % root)
        print('private folder: %s (%s)' % (root, source))
        if source.startswith('default') and root != (self.repo.parent / 'private').resolve():
            self.do('record it in %s (it is not next to this clone)' % self.config.name, self.record_private, root)
        wanted = [root / getattr(paths, name).relative_to(paths.PRIVATE_ROOT) for name in LAYOUT]
        if not (root / paths.STOCK_121.relative_to(paths.PRIVATE_ROOT) / PAIR[0]).is_file():
            wanted.append(root / 'incoming')                            # where the stock pair is copied to (Next)
        missing = [d for d in wanted if not d.is_dir()]
        if missing:
            self.do('create %s of the private layout (xy_forever/paths.py) under it'
                    % ('%d folders' % len(missing) if len(missing) > 1 else missing[0].name),
                    lambda: [d.mkdir(parents=True, exist_ok=True) for d in missing])
        self.private_root = root

    def record_private(self, root):
        global paths
        write_config(root, self.config)
        if self.config.resolve() == paths.LOCAL_CONFIG.resolve():
            import importlib
            paths = importlib.reload(paths)

    def local_files(self):
        status, single = self.repo / STATUS_INDEX, self.repo / 'docs/STATUS.md'
        print('%s: %s' % (STATUS_INDEX, 'present' if status.exists() else 'missing (local, gitignored)'))
        if not status.exists():
            if single.exists():                         # the older single local file becomes the folder's index
                self.do('move docs/STATUS.md to %s' % STATUS_INDEX, move_into, single, status)
            else:
                self.do('copy docs/STATUS.template.md to %s' % STATUS_INDEX, copy_into,
                        self.repo / 'docs/STATUS.template.md', status)
        leaks = self.settings().get('leak_patterns')
        print('%s: %s' % (self.config.name, 'no "leak_patterns" list' if leaks is None else
                          '%d leak patterns' % len(leaks)))
        if leaks is None:
            self.do('add an empty "leak_patterns" list to %s' % self.config.name, ensure_leak_patterns, self.config)

    def toolchain(self):
        folder = self.workspace / TOOLCHAIN_DIR
        if toolchain_problem() is None:
            print('toolchain: ok (authenticated against toolchains.json)')
            return
        if (folder.exists() or folder.is_symlink()) and toolchain_problem(folder) is None:
            print('toolchain: ok at %s' % folder)
            return self.toolchain_note(folder)
        if folder.exists() or folder.is_symlink():
            raise SetupError('%s exists but fails its pins (%s): move it aside, then rerun bootstrap (it never deletes)'
                             % (folder, toolchain_problem(folder)))
        host = host_name()
        record = PINS['toolchain']['archives'].get(host)
        if host == 'linux-x86_64':
            print('toolchain: missing; open-xy publishes no download for linux-x86_64: build it with Docker '
                  '(open-xy docs/build.md, "On Linux"), then put it at %s or set XYF_TOOLCHAIN' % folder)
            return
        if not record:
            print('toolchain: no pinned toolchain for %s (stock.json): use macOS arm64, Windows x86_64 or '
                  'Linux x86_64' % host)
            return
        if self.toolchain_archive and not self.toolchain_archive.is_file():
            raise SetupError('--toolchain-archive %s: no such file' % self.toolchain_archive)
        print("toolchain: missing; open-xy's toolchain-1 release for %s goes into %s" % (host, folder))
        source = ('%s (given)' % self.toolchain_archive) if self.toolchain_archive else 'download %s' % record['url']
        self.do('%s, check its SHA-256, unpack it, check every file against toolchains.json' % source,
                install_toolchain, record, folder, self.toolchain_archive)
        self.toolchain_note(folder)

    def workspace_guide(self):
        if self.workspace not in self.repo.parents:
            print('workspace AGENTS.md: skipped (%s is not inside the workspace %s)' % (self.repo, self.workspace))
            return
        repo = self.repo.relative_to(self.workspace).as_posix()
        private = (self.private_root.relative_to(self.workspace).as_posix()
                   if self.workspace in self.private_root.parents else str(self.private_root))
        for name, text in (('AGENTS.md', WORKSPACE_GUIDE % {'repo': repo, 'private': private}),
                           ('CLAUDE.md', '@AGENTS.md\n')):
            path = self.workspace / name
            if path.exists():
                print('workspace %s: present (left as it is; bootstrap never overwrites it)' % name)
            else:
                print('workspace %s: missing' % name)
                self.do('write %s (the workspace guide for agents)' % path, path.write_text, text)

    def toolchain_note(self, link):
        if self.environ.get('XYF_TOOLCHAIN'):
            print('  note: XYF_TOOLCHAIN=%s is set and fails its pins; it wins over %s: unset it'
                  % (self.environ['XYF_TOOLCHAIN'], link))
        elif self.workspace != self.repo.parent:
            print('  note: this clone is not in %s: set XYF_TOOLCHAIN=%s in every shell that builds' % (self.workspace, link))

    def npm(self):
        node, npm = shutil.which('node'), shutil.which('npm')
        for rel in NPM_DIRS:
            folder = self.repo / rel
            if (folder / 'node_modules').is_dir():
                print('%s: node_modules present' % rel)
            elif not (node and npm):
                print('%s: skipped: Node not found (install Node 22.18+ or 23.6+, then rerun bootstrap)' % rel)
            else:
                print('%s: node_modules missing' % rel)
                self.do('npm ci in %s' % rel, run, [npm, 'ci', '--no-audit', '--no-fund'], cwd=folder)

    def fixtures(self):
        if not stock_present():
            print('fixtures: waiting for the stock pair (Next, below)')
            return
        missing = fixtures_missing()
        print('fixtures: %s' % ('ok' if not missing else 'missing ' + ', '.join(missing)))
        if missing:
            self.do('run setup.py fixtures (network: public open-xy and its kit; one product build)', cmd_fixtures, None)

    def next_steps(self):
        incoming = self.private_root / 'incoming'
        steps = []
        if not stock_present():
            steps += ['Copy os.ldr and bootloader.ldr off your OP-XY (stock 1.2.1) into %s: hold COM while switching '
                      'it on, Track 3 (dev menu), Track 2 (MTP), then copy them from /upgrade/firmware/blackfin/bin/, '
                      'and /content/op1_factory.json beside them (ONBOARDING.md step 4).' % incoming,
                      'python3 scripts/setup.py import-stock %s' % incoming,
                      'python3 scripts/setup.py fixtures']
        where = ('%s (or %s)' % (self.workspace, self.repo) if (self.workspace / 'AGENTS.md').is_file()
                 and self.workspace in self.repo.parents else str(self.repo))
        steps.append('Open Claude Code in %s and say "Continue development".' % where)
        print('\nNext:')
        for n, step in enumerate(steps, 1):
            print('  %d. %s' % (n, step))


def cmd_bootstrap(args):
    return Bootstrap(args.workspace, dry_run=args.dry_run, toolchain_archive=args.toolchain_archive).run()


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='command', required=True)
    i = sub.add_parser('init', help='create the private layout')
    i.add_argument('--private', help='private folder for this clone (recorded in xyf.local.json)')
    s = sub.add_parser('import-stock', help='copy the stock 1.2.1 pair')
    s.add_argument('dir')
    sub.add_parser('fixtures', help='make test inputs from the stock pair and public open-xy')
    sub.add_parser('doctor', help='summary')
    b = sub.add_parser('bootstrap', help='set up this workspace (all that needs no OP-XY); safe to rerun')
    b.add_argument('--workspace', help="the folder that holds this clone, private/ and tools/ (default: the clone's parent)")
    b.add_argument('--dry-run', action='store_true', help='print every action without doing it')
    b.add_argument('--toolchain-archive', metavar='ZIP', help="open-xy's toolchain-1 release zip for this host, already "
                   'downloaded (default: bootstrap downloads it)')
    args = p.parse_args(argv)
    try:
        return {'init': cmd_init, 'import-stock': cmd_import_stock, 'fixtures': cmd_fixtures,
                'doctor': cmd_doctor, 'bootstrap': cmd_bootstrap}[args.command](args) or 0
    except SetupError as e:
        print('setup: ' + str(e), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
