"""Host compiles for the tests, cached by everything that goes into them.

run(argv, ...) is subprocess.run for one clang++ command line that writes one output (-o PATH).
The output is reused when the same command line (each file argument by its content, the output
path aside) meets the same compiler and every file the compile read (sources, headers, generated
.inc files, system headers: clang -M) still has the content it had: ccache's "direct mode". A
miss compiles exactly as subprocess.run would, records its stdout and stderr (replayed on a hit)
and its dependency list. A failed compile is never cached.

The cache is ../private/xy-forever/sessions/host-builds/ (never the repository; XYF_HOST_BUILDS
moves it, XYF_HOST_BUILDS=off turns it off). The KEEP most recently used entries stay. Entries are
published whole by a rename, and pruning tolerates entries other processes remove meanwhile.

observer, when set (tests/xyf_select.py), is told of every cached compile: observer(files, seconds), the
absolute paths the compile read and the seconds spent compiling (0 on a hit).

Every compile (and its -M listing) runs in its own process group, ended past bounds.COMPILE_S (#230):
subprocess.TimeoutExpired, as subprocess.run raises.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

from . import bounds, paths, procgroup

KEEP = 300
observer = None    # observer(files, seconds) after each cached compile (see above)
_hashes = {}       # (path, size, mtime_ns) -> sha256, within one process
_compilers = {}


def cache_dir():
    where = os.environ.get('XYF_HOST_BUILDS')
    if where == 'off':
        return None
    if where:
        return Path(where)
    return paths.SESSIONS / 'host-builds' if paths.PRIVATE_ROOT.is_dir() else None


def file_hash(path):
    st = os.stat(path)
    key = (str(path), st.st_size, st.st_mtime_ns)
    if key not in _hashes:
        with open(path, 'rb') as f:
            _hashes[key] = hashlib.sha256(f.read()).hexdigest()
    return _hashes[key]


def compiler(cxx):
    if cxx not in _compilers:
        _compilers[cxx] = procgroup.run([cxx, '--version'], capture_output=True, text=True,
                                        timeout=bounds.COMPILE_S).stdout
    return _compilers[cxx]


def _plan(argv, cwd):
    """(key, the -M command line, the -o index, roots) or None if not cacheable. A directory
    argument is a root, as is the folder of a file argument: dependencies under a root are
    recorded relative to it, so a generated header in a fresh temporary folder still matches."""
    argv = [str(a) for a in argv]
    if argv.count('-o') != 1 or '-fsyntax-only' in argv:
        return None
    out = argv.index('-o') + 1
    base = cwd or os.getcwd()
    words, roots = [compiler(argv[0])], []
    for i, arg in enumerate(argv):
        if i == out:
            words.append('<out>')
            continue
        path = None
        if arg.startswith('-I') and len(arg) > 2:
            arg, path = '-I', arg[2:]
        elif i and not arg.startswith('-'):
            path = arg
        p = os.path.abspath(os.path.join(base, path)) if path is not None else None
        if p is not None and os.path.isdir(p):
            roots.append(p)
            words.append('%s<dir %d>' % (arg if arg == '-I' else '', len(roots) - 1))
        elif p is not None and os.path.isfile(p):
            roots.append(os.path.dirname(p))
            words.append('%s<file %s>' % (arg if arg == '-I' else '', file_hash(p)))
        else:
            words.append(arg)
    key = hashlib.sha256(json.dumps(words).encode()).hexdigest()[:32]
    deps = argv[:out - 1] + argv[out + 1:] + ['-M', '-Wno-unused-command-line-argument']
    return key, deps, out, roots


def _portable(path, roots):
    for n, d in enumerate(roots):
        if path.startswith(d + os.sep):
            return [n, path[len(d) + 1:]]
    return [None, path]


def _local(entry, roots):
    n, path = entry
    return path if n is None else os.path.join(roots[n], path)


def _fresh(manifest, roots):
    try:
        return all(file_hash(_local(entry, roots)) == digest for entry, digest in manifest['deps'])
    except (OSError, IndexError, KeyError, TypeError):
        return False


def run(argv, check=False, capture_output=False, text=False, cwd=None, **kwargs):
    """subprocess.run(argv, ...) for a compile, through the cache when it can be."""
    root = cache_dir()
    plan = _plan(argv, cwd) if root else None
    if plan is None or kwargs:
        kwargs.setdefault('timeout', bounds.COMPILE_S)
        done = procgroup.run(argv, capture_output=capture_output, text=text, cwd=cwd, **kwargs)
        if check:
            done.check_returncode()
        return done
    key, deps_argv, out, roots = plan
    target = Path(cwd or '.') / str(argv[out])
    entry = root / key
    try:
        manifest = json.loads((entry / 'manifest.json').read_text())
        if _fresh(manifest, roots):
            # A new file, never an overwrite: macOS kills a process whose signed executable was
            # rewritten in place (a test that rebuilds and reruns the same path).
            fd, partial = tempfile.mkstemp(prefix='.' + target.name + '-', dir=target.parent)
            os.close(fd)
            shutil.copyfile(entry / 'output', partial)
            shutil.copymode(entry / 'output', partial)
            os.replace(partial, target)
            os.utime(entry)
            if observer:
                observer([_local(e, roots) for e, _ in manifest['deps']], 0.0)
            out_text, err_text = manifest['stdout'], manifest['stderr']
            return subprocess.CompletedProcess(argv, 0, *((out_text, err_text) if text else
                                                         (out_text.encode(), err_text.encode()))
                                               if capture_output else (None, None))
    except (OSError, ValueError, KeyError):
        pass
    start = time.monotonic()
    result = procgroup.run(argv, capture_output=True, cwd=cwd, timeout=bounds.COMPILE_S)
    if not capture_output:                                     # as subprocess.run would have shown them
        os.write(1, result.stdout)
        os.write(2, result.stderr)
    if result.returncode == 0:
        files = _store(root, entry, deps_argv, cwd, roots, target, result)
        if observer and files:
            observer(files, time.monotonic() - start)
    stdout, stderr = ((result.stdout.decode(errors='replace'), result.stderr.decode(errors='replace')) if text
                      else (result.stdout, result.stderr))
    done = subprocess.CompletedProcess(argv, result.returncode, *((stdout, stderr) if capture_output else (None, None)))
    if check:
        done.check_returncode()
    return done


def _store(root, entry, deps_argv, cwd, roots, target, result):
    """Publishes the entry; returns the files the compile read (None when they could not be listed)."""
    listing = procgroup.run(deps_argv, capture_output=True, text=True, cwd=cwd, timeout=bounds.COMPILE_S)
    if listing.returncode:
        return None
    names = listing.stdout.replace('\\\n', ' ').split()
    files = sorted({os.path.abspath(os.path.join(cwd or os.getcwd(), n)) for n in names if not n.endswith(':')})
    try:
        manifest = dict(deps=[[_portable(f, roots), file_hash(f)] for f in files],
                        stdout=result.stdout.decode(errors='replace'), stderr=result.stderr.decode(errors='replace'))
        root.mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix='new-', dir=root))
        shutil.copyfile(target, work / 'output')
        shutil.copymode(target, work / 'output')
        (work / 'manifest.json').write_text(json.dumps(manifest))
        _publish(root, work, entry)
    except OSError:
        return files
    prune(root)
    return files


def _publish(root, work, entry):
    """work becomes entry in one rename, never a half-written entry: a rename onto an existing (stale)
    entry fails, so that one moves aside first; another process publishing the same entry meanwhile wins."""
    try:
        work.rename(entry)
        return
    except OSError:
        pass
    aside = Path(tempfile.mkdtemp(prefix='old-', dir=root))
    try:
        entry.rename(aside / 'entry')
    except OSError:
        pass                                                   # gone already: another process moved it
    try:
        work.rename(entry)
    except OSError:
        shutil.rmtree(work, ignore_errors=True)                # another process published it first
    shutil.rmtree(aside, ignore_errors=True)


def prune(root, keep=KEEP):
    """Keeps the keep most recently used entries (and removes abandoned partial ones). Another process
    may remove an entry while this one looks: a vanished entry is skipped."""
    def used(p):
        try:
            return p.stat().st_mtime if p.is_dir() else None
        except OSError:
            return None
    entries = [(t, p) for p, t in ((p, used(p)) for p in root.iterdir()) if t is not None]
    for _, p in sorted(entries, key=lambda e: e[0], reverse=True)[keep:]:
        shutil.rmtree(p, ignore_errors=True)
