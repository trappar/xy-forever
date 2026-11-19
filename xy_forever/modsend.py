"""Mod packages on the device: `xyf_host.py mods push | list | remove`, `mods apply | cancel`.

The host only puts packages on the OP-XY; the device assembles mod firmware from them (native/mod_job).
A package set is one generation folder as `modkit split` writes it (docs/STORAGE.md): base/, <machine>/,
ext-<id>/, each with info.txt and files.sha256 listing every file of the package. On the device it goes to
/yaffs2/xy-forever/mods/<generation>/<package>/<stored name>, the flat store mod_job reads (xy_forever/modstore.py:
store.txt names its version; a file with a folder in its path is stored under its SHA-256's first 12 hex digits
and extension, so every file sits 4 folders below /yaffs2: #124, the dev menu's MTP depth limit). An older tree
(mods/<profile>/<generation>/, or a package tree without store.txt) shows in `mods list` and goes with
`mods remove <folder>` (deletes work at any depth).

Only the existing file commands are used: file.list, stage + file.write_staged (the device creates missing
folders, reads the file back and compares its CRC-32), file.read, file.delete, file.rmdir. The protocol has
no way to hash a device file without reading it, so a push skips a file by its size alone (`--verify` reads
everything back). Within a package files.sha256 is written last and deleted first whenever anything in the
package changes, so a package folder holding files.sha256 has every listed file (mod_job also checks every
file it reads against that list); stored files the new list no longer names go after it."""
import hashlib
from pathlib import Path
import re
import struct
import time
import zlib

from . import depth, modstore, package_store, usb_proto as P

ROOT = '/yaffs2/xy-forever/mods'
LIST = 'files.sha256'
INFO = 'info.txt'
TOKEN = re.compile(r'^[a-z0-9][a-z0-9._-]{0,30}$')          # info.txt generation, profile (docs/STORAGE.md)
LINE = re.compile(r'^([0-9a-f]{64})  (\S.*)$')
MAX_PATH = 239                                                # max_path 240 with the NUL
NOT_FOUND = P.STATUS_NAMES.index('not_found')


class Refused(Exception):
    """The package set or the device's answer is not what a push or remove can use."""


def info_values(data):
    return dict(l.split('=', 1) for l in data.decode('utf-8', 'replace').splitlines()
                if '=' in l and not l.startswith('#'))


def list_paths(data):
    """files.sha256 -> [(sha256 hex, path)] in file order."""
    out = []
    for line in data.decode('utf-8', 'replace').splitlines():
        m = LINE.match(line)
        if not m:
            raise Refused('%s: bad line %r' % (LIST, line[:80]))
        out.append((m.group(1), m.group(2)))
    return out


# -- the package set on the host -----------------------------------------------------------------------
def local_set(package_dir, only=()):
    """(profile, generation, {package: [(relative path, bytes)]}) for a generation folder: each package's
    files in the order they are sent (listed files, then info.txt, then files.sha256), every one checked
    against its files.sha256. only: package names (base is always included)."""
    root = Path(package_dir)
    if not (root / 'base' / INFO).is_file():
        raise Refused('%s: no base/info.txt (give the generation folder modkit split wrote)' % root)
    base = info_values((root / 'base' / INFO).read_bytes())
    profile, generation = base.get('profile', ''), base.get('generation', '')
    if base.get('type') != 'mod-base' or not TOKEN.match(profile) or not TOKEN.match(generation):
        raise Refused('base/info.txt: not a mod-base with a profile and generation')
    names = sorted(p.name for p in root.iterdir() if p.is_dir() and (p / LIST).is_file())
    wanted = ['base'] + [n for n in only if n != 'base'] if only else names
    unknown = [n for n in wanted if n not in names]
    if unknown:
        raise Refused('no such package in %s: %s' % (root, ', '.join(unknown)))
    packages = {}
    for name in sorted(set(wanted), key=lambda n: (n != 'base', n)):
        if not package_store.package_name(name):
            raise Refused('unsafe package name: %s' % name)
        folder = root / name
        entries = package_store.read_files_sha256((folder / LIST).read_bytes())
        if INFO not in [p for _, p in entries]:
            raise Refused('%s/%s does not list %s' % (name, LIST, INFO))
        files = []
        for sha, rel in entries:
            device = '%s/%s/%s/%s' % (ROOT, generation, name, modstore.stored_name(rel, sha))
            parts = rel.split('/')
            if rel == LIST or any(p in ('', '.', '..') for p in parts) or len(device.encode()) > MAX_PATH:
                raise Refused('%s: unusable path %r' % (name, rel))
            if depth.refusal(device):
                raise Refused('%s: %s' % (name, depth.refusal(device)[len('refused: '):]))
            data = (folder / rel).read_bytes() if (folder / rel).is_file() else None
            if data is None or hashlib.sha256(data).hexdigest() != sha:
                raise Refused('%s/%s: missing or differs from its %s line' % (name, rel, LIST))
            if not data:
                raise Refused('%s/%s: empty (the device stages 1 byte or more)' % (name, rel))
            files.append((rel, data))
        files.sort(key=lambda f: f[0] == INFO)                # info.txt after the other listed files
        files.append((LIST, (folder / LIST).read_bytes()))     # files.sha256 last
        try:
            modstore.stored_files(name, files)
        except modstore.Clash as error:
            raise Refused(str(error))
        fields = package_store.validate(dict(files))
        if (fields.get('generation') != generation or fields.get('profile', profile) != profile or
                fields.get('type') != ('mod-base' if name == 'base' else 'mod')):
            raise Refused('%s: generation/profile differs from its base' % name)
        packages[name] = files
    return profile, generation, packages


# -- the device ----------------------------------------------------------------------------------------
def layout_ok(proto):
    """Whether a device of this protocol reads mods/<generation>/ (1.16 and later)."""
    try:
        major, minor = (int(n) for n in proto.split('.'))
    except ValueError:
        return False
    return (major, minor) >= (1, 16)


def check(code, what):
    if code:
        raise Refused('%s: %s' % (what, P.status_name(code)))


def remote_tree(dev, folder):
    """({relative path: size} of every file below folder, [relative folders, parents first]), or None
    when folder does not exist."""
    files, dirs, todo = {}, [], ['']
    while todo:
        rel = todo.pop(0)
        code, entries = dev.list_dir(folder + ('/' + rel if rel else ''))
        if code == NOT_FOUND and not rel:
            return None
        check(code, 'file.list %s/%s' % (folder, rel))
        for kind, size, name in entries:
            path = (rel + '/' if rel else '') + name.decode('utf-8', 'replace')
            if kind == 0:
                files[path] = size
            elif kind == 1:
                dirs.append(path)
                todo.append(path)
    return files, dirs


def write_file(dev, data, path):
    code = dev.stage(data)
    check(code, 'stage %s' % path)
    code, written, back = dev.file_write(path, hashlib.sha256(data).digest(), timeout=60.0 + len(data) / 20000.0)
    check(code, 'file.write_staged %s' % path)
    if written != len(data) or back != zlib.crc32(data) & 0xffffffff:
        raise Refused('file.write_staged %s: written %d of %d, read-back CRC differs' % (path, written, len(data)))


def delete(dev, path, folder=False):
    code, error = dev.change(P.ids.REMOVE_DIR if folder else P.ids.DELETE_FILE, path)
    check(code, '%s %s (error %d)' % ('file.rmdir' if folder else 'file.delete', path, error))


def remote_hashes(dev, path):
    """{relative path: sha256 hex} from the device's files.sha256 at path ({} when absent or unreadable)."""
    code, data = dev.read_all(path, limit=1 << 20)
    if code:
        return {}
    try:
        return {rel: sha for sha, rel in list_paths(data)}
    except Refused:
        return {}


def store_check(dev, gen_path, have):
    """Writes store.txt into a new generation folder (have: its remote_tree, None when absent). Refused when the
    folder holds another layout: an older one (no store.txt) or a newer store version."""
    if have and modstore.FILE in have[0]:
        code, data = dev.read_all('%s/%s' % (gen_path, modstore.FILE), limit=1024)
        check(code, 'file.read %s/%s' % (gen_path, modstore.FILE))
        version = modstore.version_of(data)
        if version == modstore.VERSION:
            return
        if version > modstore.VERSION:
            raise Refused('%s: store version %d, newer than this tool writes: update XY Forever\'s tools' % (gen_path, version))
    if have and (have[0] or have[1]):
        raise Refused('%s holds an older layout (no %s): xyf_host.py mods remove %s --yes deletes it, then push '
                      'again' % (gen_path, modstore.FILE, gen_path.rsplit('/', 1)[1]))
    write_file(dev, modstore.TEXT, '%s/%s' % (gen_path, modstore.FILE))


def push(dev, package_dir, only=(), verify=False, out=print, by_hash=False):
    """Puts the package set on the device; returns the summary dict (sent: [(package, path)] too). Raises
    Refused (or the transport's error) on the first failure: what was written stays, and running it again
    completes it. by_hash (mods dev): a top-level file is skipped only when the device's files.sha256 lists it
    with the same SHA-256 and the device copy has the same size (a rebuilt mod often keeps its size); a stored
    name already holds its content."""
    profile, generation, packages = local_set(package_dir, only)
    gen_path = '%s/%s' % (ROOT, generation)
    start = time.monotonic()
    total = dict(sent=0, skipped=0, files=0, bytes=0, paths=[], removed=0)
    try:
        with package_store.Session(dev) as session:
            version = session.read(gen_path + '/' + modstore.FILE, optional=True)
            if version is not None and modstore.version_of(version) != modstore.VERSION:
                raise Refused('store version %d, newer or unsupported: update the tools or remove the old generation first' % modstore.version_of(version))
            if version is None:
                if session.entries(gen_path):
                    raise Refused('older layout: mods remove %s --yes, then push again' % generation)
                session.write(gen_path + '/' + modstore.FILE, modstore.TEXT)
            for name, files in packages.items():
                folder = gen_path + '/' + name
                stored = modstore.stored_files(name, files)
                physical = {stored_name: data for stored_name, _, data in stored}
                existing = remote_tree(dev, folder)
                same = existing is not None and set(existing[0]) == set(physical)
                if same:
                    for rel, data in physical.items():
                        if session.read(folder + '/' + rel) != data:
                            same = False
                            break
                if same:
                    total['skipped'] += 1
                else:
                    session.publish(folder, physical)
                    total['sent'] += 1
                    total['files'] += len(stored)
                    total['bytes'] += sum(len(data) for _, _, data in stored)
                    total['paths'] += [(name, rel) for _, rel, _ in stored]
                    total['removed'] += len(set(existing[0]) - set(physical)) if existing else 0
                out('%-22s %s %4d files (SHA-256 verified)' % (name, 'skipped' if same else 'sent', len(stored)))
        total['seconds'] = time.monotonic() - start
        out('%s/%s: %d packages sent, %d skipped; %d files, %d B in %.2f s' % (
            profile, generation, total['sent'], total['skipped'], total['files'], total['bytes'], total['seconds']))
        if verify:
            out('verify: %d files read back identical before publication' % sum(len(files) for files in packages.values()))
        return total
    except (OSError, ValueError) as error:
        raise Refused(str(error))


OLDER, NEWER = 'older', 'newer'          # holdings: a generation folder this store version does not read


def holdings(dev):
    """[(profile, generation, [(package, info dict, files, bytes, complete)], layout)] on the device; profile
    from the base's info.txt. layout None for this store, OLDER for a folder without store.txt (an older
    mods/<profile>/ folder, or a package tree from before the flat store), NEWER for a newer store version;
    neither lists packages."""
    out = []
    tree = remote_tree(dev, ROOT)
    if not tree:
        return out
    files, dirs = tree
    for gen in sorted(d for d in dirs if '/' not in d):
        version = 0
        if '%s/%s' % (gen, modstore.FILE) in files:
            code, data = dev.read_all('%s/%s/%s' % (ROOT, gen, modstore.FILE), limit=1024)
            version = modstore.version_of(data) if not code else 0
        if version != modstore.VERSION:
            out.append((None, gen, [], NEWER if version > modstore.VERSION else OLDER))
            continue
        packages = []
        for name in sorted(d.rsplit('/', 1)[1] for d in dirs if d.startswith(gen + '/') and d.count('/') == 1):
            prefix = '%s/%s/' % (gen, name)
            mine = {p[len(prefix):]: s for p, s in files.items() if p.startswith(prefix)}
            info, complete = {}, False
            if INFO in mine:
                code, data = dev.read_all('%s/%s%s' % (ROOT, prefix, INFO), limit=1 << 16)
                info = info_values(data) if not code else {}
            if LIST in mine:
                code, data = dev.read_all('%s/%s%s' % (ROOT, prefix, LIST), limit=1 << 20)
                try:
                    complete = not code and modstore.listed_names(list_paths(data)) <= set(mine)
                except Refused:
                    complete = False
            packages.append((name, info, len(mine), sum(mine.values()), complete))
        base = next((p[1] for p in packages if p[0] == 'base'), {})
        out.append((base.get('profile', '?'), gen, packages, None))
    return out


def show(dev, out=print):
    held = holdings(dev)
    if not held:
        out('no mod packages on the device (%s)' % ROOT)
    for profile, generation, packages, layout in held:
        if layout == OLDER:
            out('%s: an older layout (no %s; its files may sit too deep for the dev menu\'s MTP: #124); '
                'xyf_host.py mods remove %s --yes deletes it, then push the set again' % (generation, modstore.FILE, generation))
            continue
        if layout == NEWER:
            out('%s: a newer store version than these tools read; update XY Forever\'s tools (xyf_host.py mods remove '
                '%s --yes deletes it)' % (generation, generation))
            continue
        out('%s/%s: %d packages, %d files, %d B' % (profile, generation, len(packages),
                                                   sum(p[2] for p in packages), sum(p[3] for p in packages)))
        for name, info, count, size, complete in packages:
            what = ' '.join(v for v in (info.get('type', '?'), info.get('machine', ''), info.get('version', '')) if v)
            out('  %-22s %-26s %4d files %10d B%s' % (name, what, count, size, '' if complete else '  INCOMPLETE'))
    return held


def targets(dev, what):
    """Device folders `remove WHAT` deletes: a generation folder (WHAT names a generation) or that package
    in every generation holding it."""
    tree = remote_tree(dev, ROOT)
    dirs = tree[1] if tree else []
    if what in dirs:                                      # a generation (or an older mods/<profile>/ folder)
        return [what]
    return [d for d in dirs if d.count('/') == 1 and d.split('/')[1] == what]


def remove_folder(dev, folder):
    """Deletes folder and everything below it: files.sha256 of each package first, then the files, then
    the folders, deepest first. Returns (files, bytes) removed."""
    files, dirs = remote_tree(dev, folder) or ({}, [])
    order = sorted(files, key=lambda p: (p.rsplit('/', 1)[-1] != LIST, p))
    for rel in order:
        delete(dev, folder + '/' + rel)
    for rel in sorted(dirs, key=lambda d: -d.count('/')):
        delete(dev, folder + '/' + rel, folder=True)
    delete(dev, folder, folder=True)
    return len(files), sum(files.values())


def remove(dev, what, yes=False, out=print):
    if not yes:
        found = targets(dev, what)
        for rel in found:
            out('would remove %s/%s' % (ROOT, rel))
        out('DRY RUN: nothing deleted. Re-run with --yes to delete.')
        return 0 if found else 1
    try:
        with package_store.Session(dev) as session:
            found = targets(dev, what)
            if not found:
                out('mods remove: nothing named %r under %s' % (what, ROOT)); return 1
            for rel in found:
                folder = '%s/%s' % (ROOT, rel)
                files, _ = remote_tree(dev, folder) or ({}, [])
                session.remove(folder)
                out('removed %s: %d files, %d B' % (folder, len(files), sum(files.values())))
        return 0
    except (OSError, ValueError) as error:
        raise Refused(str(error))


# -- the device assembles (mods.apply, job.status, job.cancel) --------------------------------------
# The host only starts, watches and cancels the job; the device reads the packages and writes the entry
# into /yaffs2/xy-forever/tmp/job-<id>/ (registering it as a library entry is a later step).
PHASES = ('idle', 'inputs', 'link', 'patch', 'content', 'write', 'verify', 'finish', 'done', 'failed', 'cancelled')
ERRORS = ('none', 'argument', 'path', 'region', 'open', 'read', 'truncated', 'size', 'list', 'unlisted', 'sha256',
          'info', 'base', 'linker', 'patch', 'content', 'write', 'rename', 'verify', 'cancelled', 'image', 'link')
MISSING = ('none', "the base's os.ldr", "the base's content.sha256; library-add --content adds it",
           '/yaffs2/content/op1_factory.json',
           "the generation's base/info.txt", "the generation's store.txt (older layout: remove it, push again)")
DONE, FAILED, CANCELLED = 8, 9, 10
TMP = '/yaffs2/xy-forever/tmp'
LIBRARY = '/yaffs2/xy-forever/firmware'
DEFAULT_NAME = 'OP-XY mods'
DEFAULT_PROFILE = 'opxy-1.2.1'
STALL = 90.0            # --wait gives up when the job's step count has not moved for this long
SERVED = 60.0           # --wait then waits this long at most for file commands to be served again
# 193 (device, build 192 after an OS crash: the first job.status went unanswered): B answers job.status
# between its requests to A, and one request may take up to write_patience_ms (60 s, the default's write;
# a file request's bound is change_ms, 45 s). wait asks again after a timeout until no answer came this long.
QUIET = 65.0


def output_folder(job):
    return '%s/job-%d' % (TMP, job)


def apply_request(base, profile, generation, machines, extensions=(), name=DEFAULT_NAME, unverified=False,
                  expanded=False):
    """mods.apply's payload: six NUL-terminated strings, u32 flags (bit 0 unverified; 1.12: bit 1 expanded, the 4 MiB
    mod memory layout), the SHA-256 of all of it."""
    body = b''.join(t.encode('ascii') + b'\0' for t in (base, profile, generation, ','.join(machines),
                                                         ','.join(extensions), name))
    body += struct.pack('<I', (1 if unverified else 0) | (2 if expanded else 0))
    return body + hashlib.sha256(body).digest()


def start(dev, base, profile, generation, machines, extensions=(), name=DEFAULT_NAME, unverified=False,
          expanded=False):
    """(status, job id, missing input) of one mods.apply."""
    code, body = dev.call(P.ids.MODS_APPLY, apply_request(base, profile, generation, machines, extensions, name,
                                                          unverified, expanded),
                          timeout=P.ids.COMMANDS[P.ids.MODS_APPLY]['timeout_ms'] / 1000.0)
    job, missing = struct.unpack_from('<II', body) if len(body) >= 8 else (0, 0)
    return code, job, missing


def job_status(dev, job=0):
    """(status, {field: value} with detail as text, or None)."""
    code, body = dev.call(P.ids.JOB_STATUS, struct.pack('<I', job),
                          timeout=P.ids.COMMANDS[P.ids.JOB_STATUS]['timeout_ms'] / 1000.0)
    _, fields = P.decode_reply(P.ids.JOB_STATUS, struct.pack('<I', code) + body)
    if fields is not None:
        fields['detail'] = bytes(fields['detail']).split(b'\0', 1)[0].decode('ascii', 'replace')
    return code, fields


def job_cancel(dev, job=0):
    """(status, debris)."""
    code, body = dev.call(P.ids.JOB_CANCEL, struct.pack('<I', job),
                          timeout=P.ids.COMMANDS[P.ids.JOB_CANCEL]['timeout_ms'] / 1000.0)
    return code, struct.unpack_from('<I', body)[0] if len(body) >= 4 else 0


def describe(f):
    error = ERRORS[f['error']] if f['error'] < len(ERRORS) else str(f['error'])
    return '%s %d%s%s, %.1f s, %d steps%s%s' % (
        PHASES[f['phase']] if f['phase'] < len(PHASES) else f['phase'], f['done'],
        '/%d' % f['total'] if f['total'] else '', (' (%s %d %s)' % (error, f['code'], f['detail'])) if f['error'] else '',
        f['elapsed_ms'] / 1000.0, f['steps'], ', longest pass %d ms' % f['longest_ms'],
        ', leftovers may remain' if f['debris'] else '')


def wait(dev, job, out=print, poll=0.5, sleep=time.sleep, clock=time.monotonic):
    """Polls job.status until the job is final; prints each change of phase. The final fields, or None
    when the job vanished (not_found) or stalled STALL seconds. A job.status without an answer is asked
    again until none came for QUIET seconds (then TimeoutError)."""
    shown, steps, moved = None, -1, clock()
    answered, silent = clock(), False
    while True:
        try:
            code, f = job_status(dev, job)
        except TimeoutError:                       # 193: B inside one long request to A: ask again
            if clock() - answered > QUIET:
                raise
            if not silent:
                out('job %d: no answer yet (storage is busy); asking again for up to %d s' % (job, QUIET))
            silent = True
            continue
        answered, silent = clock(), False
        if code:
            out('job %d: %s' % (job, P.status_name(code)))
            return None
        if f['steps'] != steps:
            steps, moved = f['steps'], clock()
        if f['phase'] != shown or f['phase'] in (DONE, FAILED, CANCELLED):
            shown = f['phase']
            out('job %d: %s' % (job, describe(f)))
        if f['phase'] in (DONE, FAILED, CANCELLED):
            return f
        if clock() - moved > STALL:
            out('job %d: no progress for %d s (steps %d); stopped waiting (mods cancel stops it)' % (job, STALL, steps))
            return None
        sleep(poll)


def served(dev, out=print, poll=0.25, sleep=time.sleep, clock=time.monotonic):
    """When a job ends, B closes A's write window (op 9; yaffs's checkpoint took up to about 17 s on
    the device) while USB keeps answering; file commands answer busy until it is closed. Lists tmp/ until
    the answer is not busy: that status (0 or not_found: served), or None after SERVED seconds."""
    start = clock()
    while True:
        code = dev.list_page(TMP)[0]
        if code != P.ids.S_BUSY:
            if clock() - start >= 1.0:
                out('storage window closed %.1f s after the job ended' % (clock() - start))
            return code
        if clock() - start > SERVED:
            out('file commands still busy %d s after the job ended' % SERVED)
            return None
        sleep(poll)


LIBRARY_ID = re.compile(r'^[a-z0-9_-]{1,31}$')                # xyf_store valid_id: a package's id
MOST_MACHINES, MOST_EXTENSIONS = 64, 8                        # mods.apply (usb_service mods_begin)
MACHINES_BYTES, EXTENSIONS_BYTES, NAME_BYTES = 1023, 255, 47  # its fields, without the NUL


def split_ids(words):
    """The ids in command-line words, each split at commas and white space: zsh passes an unquoted
    variable holding 'a b c' as one word, which the device refused (bad_request) as one invalid id."""
    return [i for w in words for i in re.split(r'[\s,]+', w) if i]


def selection_refusal(machines, extensions, name):
    """Why mods.apply would refuse this selection (the reason, naming the limit), or None."""
    for kind, ids, most, field in (('machine', machines, MOST_MACHINES, MACHINES_BYTES),
                                   ('extension', extensions, MOST_EXTENSIONS, EXTENSIONS_BYTES)):
        for i in ids:
            if not LIBRARY_ID.match(i):
                return '%s id %r: 1-31 of a-z 0-9 _ -' % (kind, i)
            if ids.count(i) > 1:
                return '%s %s given twice' % (kind, i)
        if len(ids) > most:
            return '%d %ss: at most %d in one build' % (len(ids), kind, most)
        if len(','.join(ids)) > field:
            return 'the %s list is %d bytes: at most %d' % (kind, len(','.join(ids)), field)
    if not machines:
        return 'no machine: at least one'
    if not (0 < len(name) <= NAME_BYTES and name.strip() == name and all(' ' <= c <= '~' for c in name)):
        return 'name %r: 1-%d printable ASCII bytes, no space at either end' % (name, NAME_BYTES)
    return None


def apply_command(dev, args, out=print):
    profile, _, generation = args.generation.rpartition('/')
    expanded = getattr(args, 'memory', 'standard') == 'expanded'
    code, job, missing = start(dev, args.base, profile or args.profile, generation, args.machines, args.ext,
                               args.name, args.unverified, expanded)
    if code:
        out('mods apply: %s%s%s' % (P.status_name(code),
                                    ' (missing %s)' % MISSING[missing] if missing and code == NOT_FOUND else '',
                                    ' (--memory expanded needs XY Forever with protocol 1.12 or later)'
                                    if expanded and code == P.ids.S_BAD_REQUEST else ''))
        return code
    out('mods apply: job %d started; output %s/' % (job, output_folder(job)))
    if not args.wait:
        return 0
    f = wait(dev, job, out)
    if f is None:
        return 1
    code = served(dev, out)                                              # the next command finds A served
    if code not in (0, P.ids.S_NOT_FOUND):
        out('mods apply: file commands after the job: %s' % ('busy' if code is None else P.status_name(code)))
        return 1
    if f['phase'] == DONE:
        out('mods apply: done; library entry %s (%s/%s/; its content is not installed yet, so it does not boot)'
            % (f['detail'], LIBRARY, f['detail']))
        return 0
    return 1


# -- xyf_host.py hook ----------------------------------------------------------------------------------
def add_arguments(parser):
    sub = parser.add_subparsers(dest='mods_command', required=True)
    p = sub.add_parser('push', help='put a package set (a generation folder from modkit split) on the device')
    p.add_argument('package_dir', type=Path)
    p.add_argument('--only', nargs='+', default=(), metavar='ID', help='these packages only (base is always sent)')
    p.add_argument('--verify', action='store_true', help='read every file back and compare')
    sub.add_parser('list', help='generations and packages on the device, with sizes')
    f = sub.add_parser('fetch', help='download packages from a package index into a generation folder (no device)')
    f.add_argument('index', metavar='INDEX_URL_OR_DIR', help='index.json or its folder: a path, file://, http(s)://')
    f.add_argument('--only', nargs='+', default=(), metavar='ID', help='these packages only (base always comes)')
    f.add_argument('--generation', metavar='[PROFILE/]GENERATION', help='when the index holds more than one')
    f.add_argument('--to', required=True, type=Path, help='the generation folder to write (new or empty)')
    r = sub.add_parser('remove', help='delete a generation, or a package from every generation holding it')
    r.add_argument('what', metavar='GENERATION|ID')
    r.add_argument('--yes', action='store_true', help='delete (default: dry run)')
    a = sub.add_parser('apply', help='assemble mod firmware on the device into library entry mods-a or mods-b')
    a.add_argument('--base', required=True, help='library entry id holding the stock os.ldr (and content.sha256)')
    a.add_argument('--generation', required=True, metavar='[PROFILE/]GENERATION', help='the package set on the device')
    a.add_argument('--profile', default=DEFAULT_PROFILE, help='when --generation names none (default %(default)s)')
    a.add_argument('machines', nargs='+', metavar='MACHINE', help='machine ids (1-64; a word may hold several, '
                   'separated by commas or spaces)')
    a.add_argument('--ext', nargs='+', default=[], metavar='ID', help='extension ids (0-8, as MACHINE)')
    a.add_argument('--name', default=DEFAULT_NAME, help='the entry name (default %(default)r)')
    a.add_argument('--unverified', action='store_true', help='the user confirmed a base that is not a known one')
    a.add_argument('--memory', choices=('standard', 'expanded'), default='standard',
                   help='mod memory layout: expanded is the 4 MiB option (protocol 1.12; default %(default)s)')
    a.add_argument('--wait', action='store_true', help='poll job.status until it ends; non-zero exit on failure')
    c = sub.add_parser('cancel', help='stop the running assembly job (it removes what it wrote)')
    c.add_argument('job', nargs='?', type=int, default=0, help='job id (default: the running job)')
    from . import moddev                                     # mods dev (the edit-build-boot loop)
    moddev.add_arguments(sub)


def command(args, connect):
    """`mods ...` with xyf_host's connect(timeout) -> (Device, port, hello text)."""
    if args.mods_command == 'fetch':                           # host only: never connects
        from . import modindex
        try:
            modindex.fetch(args.index, args.to, args.only, args.generation)
        except (modindex.Refused, Refused, OSError) as error:
            print('mods fetch: refused: %s' % error)
            return 2
        return 0
    if args.mods_command == 'push':
        try:
            local_set(args.package_dir, args.only)             # refuse a bad set before connecting
        except (Refused, OSError) as error:
            print('mods push: refused: %s' % error)
            return 2
    if args.mods_command == 'apply':                           # refuse a selection the device would, first
        args.machines, args.ext = split_ids(args.machines), split_ids(args.ext)
        why = selection_refusal(args.machines, args.ext, args.name)
        if why:
            print('mods apply: refused: %s' % why)
            return 2
    dev, port, text = connect(args.timeout)
    print('connected %s: %s' % (port, text))
    if P.Hello(text).major != '1':
        print('refused: the device speaks protocol %s; this tool speaks 1.x' % P.Hello(text).proto)
        return 2
    if args.mods_command in ('push', 'apply') and not layout_ok(P.Hello(text).proto):
        print('refused: this XY Forever (protocol %s) reads mod packages from mods/<profile>/<generation>/; '
              'install a build with protocol 1.16 or later first (#124)' % P.Hello(text).proto)
        return 2
    try:
        if args.mods_command == 'push':
            push(dev, args.package_dir, args.only, args.verify)
            return 0
        if args.mods_command == 'list':
            show(dev)
            return 0
        if args.mods_command == 'apply':
            return apply_command(dev, args)
        if args.mods_command == 'cancel':
            code, debris = job_cancel(dev, args.job)
            print('mods cancel: %s%s' % (P.status_name(code), '; leftovers may remain in tmp/' if debris else ''))
            return code
        return remove(dev, args.what, args.yes)
    except Refused as error:
        print('mods %s: %s' % (args.mods_command, error))
        return 1
    except (OSError, TimeoutError) as error:
        print('mods %s: connection lost (%s); what was written stays, run it again' % (args.mods_command, error))
        return 1
