"""The pinned contributor tools and the dependencies they install: setup.py doctor's rows and bootstrap's steps.

mise installs the tools mise.toml pins (Python, uv, Node, pnpm, gh; checksums in mise.lock), `uv sync` the
Python dependencies (pyproject.toml, uv.lock, into .venv), `pnpm install` the JS ones (one pnpm-lock.yaml for
the workspace: web/ and clients/ts), `lefthook install` the git pre-commit hook (lefthook.yml). Every call keeps
the contributor's global mise config out (IGNORE)."""

import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MISE_URL = 'https://mise.jdx.dev/installing-mise.html'


def env(environ=None):
    """environ with the global mise config ignored (a project cannot hide it itself; mise.toml [env] says why)."""
    environ = dict(os.environ if environ is None else environ)
    environ['MISE_IGNORED_CONFIG_PATHS'] = str(Path.home() / '.config/mise')
    return environ


def mise():
    return shutil.which('mise')


def tool(name):
    """The command that runs a pinned tool: through `mise exec` (works without an activated shell), else PATH."""
    m = mise()
    if m:
        return [m, 'exec', '--', name]
    found = shutil.which(name)
    return [found] if found else None


def quiet(cmd, root=ROOT, timeout=120):
    try:
        return subprocess.run(cmd, cwd=root, env=env(), capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return subprocess.CompletedProcess(cmd, 1, '', str(e))


def tools_missing(root=ROOT):
    """None when every pin of mise.toml is installed, else why not (mise absent, untrusted, tools missing)."""
    m = mise()
    if not m:
        return 'mise not found: install it (%s), then `mise trust && mise install` here' % MISE_URL
    run = quiet([m, 'ls', '--current', '--missing', '--json'], root)
    if run.returncode:
        last = (run.stderr.strip().splitlines() or ['mise ls failed'])[0]
        return '%s (`mise trust` here first?)' % last
    try:
        missing = sorted(json.loads(run.stdout or '{}'))
    except ValueError:
        return 'mise ls: unreadable output'
    return 'missing %s: run `mise install`' % ', '.join(missing) if missing else None


def python_deps_stale(root=ROOT):
    """None when .venv matches uv.lock, else what to run."""
    uv = tool('uv')
    if not uv:
        return 'uv not found: run `mise install`'
    return None if quiet(uv + ['sync', '--locked', '--check'], root).returncode == 0 else 'run `uv sync`'


def js_deps_stale(root=ROOT):
    """None when node_modules was installed from the current pnpm-lock.yaml (by mtime), else what to run."""
    marker, lock = root / 'node_modules/.modules.yaml', root / 'pnpm-lock.yaml'
    if not marker.is_file() or not all((root / rel / 'node_modules').is_dir() for rel in ('web', 'clients/ts')):
        return 'run `pnpm install`'
    return None if marker.stat().st_mtime >= lock.stat().st_mtime else 'pnpm-lock.yaml changed: run `pnpm install`'


def js_install(root=ROOT):
    """The pnpm install command when node_modules is missing or stale (a fresh worktree), else None."""
    return [shutil.which('pnpm') or 'pnpm', 'install', '--frozen-lockfile'] if js_deps_stale(root) else None


def hook_missing(root=ROOT):
    """None when the pre-commit hook runs lefthook (or root is no git checkout: nothing to install), else what to run."""
    run = quiet(['git', 'rev-parse', '--git-path', 'hooks/pre-commit'], root)
    if run.returncode:
        return None
    hook = Path(root) / run.stdout.strip()
    installed = hook.is_file() and 'lefthook' in hook.read_text(errors='replace')
    return None if installed else 'run `lefthook install`'


def doctor_rows(root=ROOT):
    """[(label, ok, detail)]: the tools, then the Python and JS dependencies."""
    m = mise()
    version = quiet([m, '--version'], root).stdout.split()[:1] if m else []
    rows = [('mise', bool(m), version[0] if version else 'not found: %s' % MISE_URL)]
    for label, problem, fine in (
        ('tools', tools_missing(root), 'mise.toml pins installed'),
        ('python deps', python_deps_stale(root), '.venv matches uv.lock'),
        ('js deps', js_deps_stale(root), 'node_modules matches pnpm-lock.yaml'),
        ('git hook', hook_missing(root), 'pre-commit runs lefthook (lefthook.yml)'),
    ):
        rows.append((label, problem is None, problem or fine))
    return rows


def bootstrap_steps(do, run, root=ROOT):
    """setup.py bootstrap's tool steps, each only when needed: do(what, action, *args, **kw) records and runs
    it (or only prints it in a dry run); run(cmd, cwd=, env=) runs a command."""
    if not mise():
        print('tools: skipped: %s' % tools_missing(root))
        return
    problem = tools_missing(root)
    print('tools: %s' % (problem or 'ok (mise.toml pins installed)'))
    if problem and 'run `mise install`' in problem:
        do('mise install (the pins of mise.toml)', run, [mise(), 'install'], cwd=root, env=env())
    elif problem:
        return  # untrusted or broken: the message says what to do
    for label, stale, cmd in (
        ('python deps', python_deps_stale, ['uv', 'sync', '--locked']),
        ('js deps', js_deps_stale, ['pnpm', 'install', '--frozen-lockfile']),
        ('git hook', hook_missing, ['lefthook', 'install']),
    ):
        why = stale(root)
        print('%s: %s' % (label, why or 'ok'))
        if why:
            do(' '.join(cmd), run, tool(cmd[0]) + cmd[1:], cwd=root, env=env())
