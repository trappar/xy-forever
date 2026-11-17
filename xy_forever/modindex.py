"""A local package index for mod packages: `xyf_package.py index build | verify`,
`xyf_host.py mods fetch`.

`index build GENERATION_DIR --out DIR` turns one generation folder (as `modkit split` writes it, checked
like `mods push` checks it) into DIR/index.json plus one archive per package at
DIR/<profile>/<generation>/<package>.zip. Building into a DIR that already holds an index adds (or
replaces) that generation. Archives are ZIP files with STORED members (no compression), in byte order of
their names, every timestamp 1980-01-01 00:00:00, fixed attributes and no extra fields, so the same
package always gives the same bytes and SHA-256 on any machine.

Nothing is published: the tool only writes local files. The licence and redistribution decision for a
hosted index is not made, so every package's `licence` and `source` are explicit placeholders
(PLACEHOLDER); the package's own info.txt (inside the archive) still says what its kit said.

`mods fetch INDEX_URL_OR_DIR [--only ID...] --to DIR` reads index.json (file://, http://, https:// or a
folder), downloads the base and the chosen packages, checks every size and SHA-256 and writes the
generation folder DIR byte-identical to the one the index was built from, ready for `mods push DIR`.
It refuses an index or archive that is not exactly what it should be: unknown format, bad ids, absolute
or traversing or over-long paths, members not listed, duplicate or compressed members, sizes over the
limits, a hash that differs.

index.json (format xyf-mod-index/1):
  {"format": "xyf-mod-index/1", "publish": false, "notice": ..., "profiles": [{"profile": P,
   "generations": [{"generation": G, "base": "base", "packages": [PACKAGE...]}]}]}
  PACKAGE: {"id", "name", "kind" (base | machine | extension), "machine", "version",
   "memory": {"code", "data", "state", "total"} (bytes, from info.txt; 0 when not given),
   "content_bytes" (sum of file sizes), "files": [{"path", "size", "sha256"}] (byte order of path),
   "archive": {"name" (relative to index.json), "size", "sha256"},
   "licence": PLACEHOLDER, "source": PLACEHOLDER}
"""
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import struct
import urllib.parse
import urllib.request
import zipfile
import zlib

from . import modsend as S

FORMAT = 'xyf-mod-index/1'
PLACEHOLDER = 'NOT DECIDED (no licence or redistribution decision for a hosted index; local use only)'
NOTICE = ('Built locally by xyf_package.py index build. Not for publication: the licence and redistribution '
          'decision for a hosted index is not made.')
ID = re.compile(r'^[a-z0-9][a-z0-9._-]{0,30}$')       # package folder names (modkit: base, <machine>, ext-<id>)
SHA = re.compile(r'^[0-9a-f]{64}$')
STAMP = (1980, 1, 1, 0, 0, 0)
MAX_INDEX = 16 << 20                                   # index.json
MAX_FILE = 32 << 20                                    # one member
MAX_ARCHIVE = 256 << 20                                # one archive
MAX_FILES = 4096                                       # files.sha256 limit on the device (docs/STORAGE.md)
KINDS = ('base', 'machine', 'extension')


class Refused(Exception):
    """The generation folder, index or archive is not one this tool accepts."""


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def number(value):
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


# -- building ------------------------------------------------------------------------------------------
def archive_bytes(files):
    """Deterministic ZIP of {path: bytes}: STORED, byte order, fixed stamp and attributes."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_STORED) as z:
        for path in sorted(files, key=str.encode):
            info = zipfile.ZipInfo(path, STAMP)
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            z.writestr(info, files[path])
    return out.getvalue()


def package_files(gen, name):
    """{path: bytes} of one package folder: exactly its files.sha256 and every file it lists."""
    folder = Path(gen) / name
    listed = [rel for _, rel in S.list_paths((folder / S.LIST).read_bytes())]
    found = sorted(p.relative_to(folder).as_posix() for p in folder.rglob('*') if p.is_file())
    extra = sorted(set(found) - set(listed) - {S.LIST})
    if extra:
        raise Refused('%s: files not in its %s: %s' % (name, S.LIST, ', '.join(extra[:5])))
    return {rel: (folder / rel).read_bytes() for rel in listed + [S.LIST]}


def package_entry(gen, profile, generation, name, files):
    info = S.info_values(files[S.INFO])
    kind = 'base' if info.get('type') == 'mod-base' else 'extension' if name.startswith('ext-') else 'machine'
    memory = {k: number(info.get(k)) for k in ('code', 'data', 'state')}
    memory['total'] = sum(memory.values())
    data = archive_bytes(files)
    return {
        'id': name, 'name': info.get('name', name), 'kind': kind, 'machine': info.get('machine', ''),
        'version': info.get('version', ''), 'memory': memory,
        'content_bytes': sum(len(b) for b in files.values()),
        'files': [{'path': p, 'size': len(files[p]), 'sha256': sha256(files[p])} for p in sorted(files, key=str.encode)],
        'archive': {'name': '%s/%s/%s.zip' % (profile, generation, name), 'size': len(data), 'sha256': sha256(data)},
        'licence': PLACEHOLDER, 'source': PLACEHOLDER,
    }, data


def build(gen_dir, out_dir):
    """Writes out_dir/index.json and the archives; returns the generation's index record."""
    try:
        profile, generation, packages = S.local_set(gen_dir)       # every listed file checked
    except S.Refused as error:
        raise Refused(str(error)) from None
    out = Path(out_dir)
    index_path = out / 'index.json'
    index = load_local(out) if index_path.is_file() else {'format': FORMAT, 'publish': False, 'notice': NOTICE,
                                                          'profiles': []}
    entries = []
    for name in packages:
        if not ID.match(name):
            raise Refused('package folder name %r is not an id' % name)
        files = package_files(gen_dir, name)
        entry, data = package_entry(gen_dir, profile, generation, name, files)
        target = out / entry['archive']['name']
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        entries.append(entry)
    record = {'generation': generation, 'base': 'base', 'packages': entries}
    prof = next((p for p in index['profiles'] if p['profile'] == profile), None)
    if prof is None:
        prof = {'profile': profile, 'generations': []}
        index['profiles'].append(prof)
    prof['generations'] = [g for g in prof['generations'] if g['generation'] != generation] + [record]
    prof['generations'].sort(key=lambda g: g['generation'])
    index['profiles'].sort(key=lambda p: p['profile'])
    index_path.write_text(json.dumps(index, indent=1, sort_keys=True) + '\n')
    return record


# -- checking an index (from anywhere) -----------------------------------------------------------------
def check_path(profile, generation, package, rel):
    parts = rel.split('/')
    device = '%s/%s/%s/%s/%s' % (S.ROOT, profile, generation, package, rel)
    if (not rel or rel.startswith('/') or '\\' in rel or any(p in ('', '.', '..') for p in parts) or
            ':' in parts[0] or any(ord(c) < 0x20 or ord(c) == 0x7f for c in rel)):
        raise Refused('%s: unusable path %r' % (package, rel[:80]))
    if len(device.encode()) > S.MAX_PATH:
        raise Refused('%s: path too long for the device (%d bytes): %r' % (package, len(device.encode()), rel[:80]))


def need(cond, what):
    if not cond:
        raise Refused(what)


def check_index(index):
    """Raises Refused unless index is a well-formed xyf-mod-index/1; returns it."""
    need(isinstance(index, dict) and index.get('format') == FORMAT, 'not a %s index' % FORMAT)
    need(isinstance(index.get('profiles'), list), 'index: no profiles list')
    for prof in index['profiles']:
        need(isinstance(prof, dict) and isinstance(prof.get('profile'), str) and S.TOKEN.match(prof['profile']),
             'index: bad profile')
        need(isinstance(prof.get('generations'), list), 'index: no generations list')
        for gen in prof['generations']:
            need(isinstance(gen, dict) and isinstance(gen.get('generation'), str) and
                 S.TOKEN.match(gen['generation']), 'index: bad generation')
            need(isinstance(gen.get('packages'), list), 'index: no packages list')
            ids = [p.get('id') if isinstance(p, dict) else None for p in gen['packages']]
            need(len(set(ids)) == len(ids) and 'base' in ids, 'index %s: duplicate ids or no base' % gen['generation'])
            for pkg in gen['packages']:
                check_package(prof['profile'], gen['generation'], pkg)
    return index


def check_package(profile, generation, pkg):
    pid = pkg.get('id')
    need(isinstance(pid, str) and ID.match(pid), 'index: bad package id %r' % (str(pid)[:40],))
    need(pkg.get('kind') in KINDS, '%s: bad kind' % pid)
    files = pkg.get('files')
    need(isinstance(files, list) and 0 < len(files) <= MAX_FILES + 1, '%s: bad file list' % pid)
    seen = set()
    for f in files:
        need(isinstance(f, dict) and isinstance(f.get('path'), str), '%s: bad file entry' % pid)
        check_path(profile, generation, pid, f['path'])
        need(f['path'] not in seen, '%s: %s listed twice' % (pid, f['path']))
        seen.add(f['path'])
        need(isinstance(f.get('size'), int) and 0 < f['size'] <= MAX_FILE, '%s/%s: bad or oversize size' % (pid, f['path']))
        need(isinstance(f.get('sha256'), str) and SHA.match(f['sha256']), '%s/%s: bad sha256' % (pid, f['path']))
    need(S.LIST in seen and S.INFO in seen, '%s: no %s or %s' % (pid, S.LIST, S.INFO))
    clash = sorted(seen & {'/'.join(p.split('/')[:i]) for p in seen for i in range(1, p.count('/') + 1)})
    need(not clash, '%s: %s is both a file and a folder' % (pid, clash[:1]))
    arc = pkg.get('archive')
    need(isinstance(arc, dict) and arc.get('name') == '%s/%s/%s.zip' % (profile, generation, pid),
         '%s: archive name is not %s/%s/%s.zip' % (pid, profile, generation, pid))
    need(isinstance(arc.get('size'), int) and 0 < arc['size'] <= MAX_ARCHIVE, '%s: bad or oversize archive' % pid)
    need(isinstance(arc.get('sha256'), str) and SHA.match(arc['sha256']), '%s: bad archive sha256' % pid)


def unpack(pkg, data):
    """{path: bytes} of an archive, checked against the package record (refuses anything unlisted)."""
    pid, arc = pkg['id'], pkg['archive']
    need(len(data) == arc['size'] and sha256(data) == arc['sha256'],
         '%s: archive differs from the index (size %d, sha256 %s)' % (pid, len(data), sha256(data)[:16]))
    want = {f['path']: f for f in pkg['files']}
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        infos = z.infolist()
    except (zipfile.BadZipFile, ValueError, struct.error) as error:
        raise Refused('%s: not a usable archive (%s)' % (pid, error)) from None
    names = [i.filename for i in infos]
    need(len(set(names)) == len(names), '%s: duplicate archive members' % pid)
    unknown = sorted(set(names) - set(want))
    need(not unknown, '%s: archive members not in the index: %s' % (pid, ', '.join(n[:60] for n in unknown[:5])))
    missing = sorted(set(want) - set(names))
    need(not missing, '%s: archive lacks %s' % (pid, ', '.join(missing[:5])))
    files = {}
    for info in infos:
        f = want[info.filename]
        need(info.compress_type == zipfile.ZIP_STORED, '%s/%s: compressed member' % (pid, info.filename))
        need(info.file_size == f['size'] and info.compress_size == f['size'],
             '%s/%s: member size %d, index says %d' % (pid, info.filename, info.file_size, f['size']))
        try:
            with z.open(info) as member:
                body = member.read(f['size'] + 1)
        except (zipfile.BadZipFile, zlib.error, OSError, ValueError) as error:
            raise Refused('%s/%s: unreadable member (%s)' % (pid, info.filename, error)) from None
        need(len(body) == f['size'] and sha256(body) == f['sha256'], '%s/%s: differs from the index' % (pid, info.filename))
        files[info.filename] = body
    listed = {rel: sha for sha, rel in S.list_paths(files[S.LIST])}
    need(listed == {p: f['sha256'] for p, f in want.items() if p != S.LIST},
         '%s: its %s does not match the index' % (pid, S.LIST))
    return files


# -- reading an index from a folder or URL -------------------------------------------------------------
def index_url(where):
    """The URL of index.json for a folder, a file path, or a URL (of index.json or of its folder)."""
    where = str(where)
    if '://' not in where:
        path = Path(where).resolve()
        where = (path / 'index.json' if path.is_dir() else path).as_uri()
    elif not where.endswith('.json'):
        where = where.rstrip('/') + '/index.json'
    scheme = urllib.parse.urlsplit(where).scheme
    need(scheme in ('file', 'http', 'https'), 'unsupported URL scheme %r' % scheme)
    return where


def get(url, limit, timeout=60.0):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as answer:
            data = answer.read(limit + 1)
    except (OSError, ValueError) as error:
        if hasattr(error, 'close'):
            error.close()                                      # an HTTPError holds the answer open
        raise Refused('cannot read %s (%s)' % (url, error)) from None
    need(len(data) <= limit, '%s: larger than %d bytes' % (url, limit))
    return data


def parse(data, where):
    try:
        return check_index(json.loads(data.decode('utf-8')))
    except (UnicodeDecodeError, ValueError) as error:
        raise Refused('%s: not JSON (%s)' % (where, error)) from None


def load_local(folder):
    return parse((Path(folder) / 'index.json').read_bytes(), folder)


def pick(index, choice=None):
    """(profile, generation record) named by choice ('[PROFILE/]GENERATION'), or the only one."""
    gens = [(p['profile'], g) for p in index['profiles'] for g in p['generations']]
    if choice:
        profile, _, name = choice.rpartition('/')
        gens = [(p, g) for p, g in gens if g['generation'] == name and (not profile or p == profile)]
    if len(gens) != 1:
        raise Refused('choose one generation with --generation: %s' % ', '.join(
            '%s/%s' % (p, g['generation']) for p, g in
            [(p['profile'], g) for p in index['profiles'] for g in p['generations']]) or 'none in the index')
    return gens[0]


def verify(folder, out=print):
    """Every archive of a local index matches it; returns the number of packages checked."""
    index = load_local(folder)
    count = 0
    for prof in index['profiles']:
        for gen in prof['generations']:
            for pkg in gen['packages']:
                path = Path(folder) / pkg['archive']['name']
                need(path.is_file(), '%s: no archive %s' % (pkg['id'], pkg['archive']['name']))
                unpack(pkg, path.read_bytes())
                count += 1
            out('%s/%s: %d packages verified' % (prof['profile'], gen['generation'], len(gen['packages'])))
    return count


def fetch(where, to, only=(), generation=None, out=print):
    """Downloads the base and chosen packages and writes the generation folder `to` (which must not exist,
    or be an empty folder). Returns (profile, generation, [package ids])."""
    url = index_url(where)
    index = parse(get(url, MAX_INDEX), url)
    profile, gen = pick(index, generation)
    by_id = {p['id']: p for p in gen['packages']}
    unknown = [i for i in only if i not in by_id]
    need(not unknown, 'not in the index: %s' % ', '.join(unknown))
    ids = ['base'] + sorted(set(only) - {'base'}) if only else sorted(by_id, key=lambda i: (i != 'base', i))
    target = Path(to)
    need(not target.exists() or (target.is_dir() and not any(target.iterdir())),
         '%s exists and is not empty' % target)
    got = {}
    for pid in ids:
        pkg = by_id[pid]
        data = get(urllib.parse.urljoin(url, pkg['archive']['name']), pkg['archive']['size'])
        got[pid] = unpack(pkg, data)
        out('%-22s %4d files %10d B' % (pid, len(got[pid]), sum(map(len, got[pid].values()))))
    info = S.info_values(got['base'][S.INFO])
    need((info.get('profile'), info.get('generation')) == (profile, gen['generation']),
         'base/info.txt names %s/%s, the index %s/%s' % (info.get('profile'), info.get('generation'), profile,
                                                          gen['generation']))
    partial = target.with_name(target.name + '.partial')
    shutil.rmtree(partial, ignore_errors=True)
    for pid, files in got.items():
        for rel, body in files.items():
            path = partial / pid / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
    try:
        S.local_set(partial)                                        # what `mods push` will check
    except S.Refused as error:
        shutil.rmtree(partial, ignore_errors=True)
        raise Refused('fetched set: %s' % error) from None
    if target.exists():
        target.rmdir()
    os.replace(partial, target)
    out('%s/%s: %d packages written to %s' % (profile, gen['generation'], len(got), target))
    return profile, gen['generation'], ids
