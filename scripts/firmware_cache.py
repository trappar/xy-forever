#!/usr/bin/env python3
"""The product build and its --verify through Turborepo's local cache (mise run build, verify; check.py).

    uv run scripts/firmware_cache.py build [--force]     the product build into build/product (gitignored)
    uv run scripts/firmware_cache.py verify [--force]    the build, then xyf_build.py --verify of it
    uv run scripts/firmware_cache.py key                 the outside-the-checkout part of the cache key

Turborepo (turbo.json: //#firmware-build, //#firmware-verify) hashes the files the build reads in the checkout
(its inputs: xyf_build.py own_sources(), which tests/test_firmware_cache.py holds turbo.json to) and, through
XYF_BUILD_KEY, what it reads outside it: the stock pair's SHA-256, the toolchain (where it is and the pins it must
match in toolchains.json), the Python that runs the build and the checkout's path (commands.json records it).
This script computes that key on every run, so nobody has to set it. Same hash: turbo restores build/product and
replays the logs in about 0.2 s; else it runs the task. A failed task (a build or --verify that exits non-zero)
is never cached. A hit restores bytes that already passed --verify (a build runs it before it finishes).
The cache is local only: .turbo/cache (gitignored; remote caching is off in turbo.json and here), as it holds
Teenage Engineering-derived bytes. --force runs the tasks past the cache; deleting .turbo/ clears it.

run-build and run-verify are the task commands turbo runs (package.json scripts).
"""

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from importlib import metadata
from pathlib import Path

from xy_forever import dev_tools, paths, toolchain

ROOT = Path(__file__).resolve().parents[1]

OUT = paths.BUILD / 'product'  # the task's output (turbo.json outputs), gitignored
TASKS = {'build': 'firmware-build', 'verify': 'firmware-verify'}
KEY = 'XYF_BUILD_KEY'
TURBO = ROOT / 'node_modules' / '.bin' / ('turbo.cmd' if os.name == 'nt' else 'turbo')
ENV = {'TURBO_TELEMETRY_DISABLED': '1', 'TURBO_NO_UPDATE_NOTIFIER': '1'}


def file_sha(path):
    try:
        return toolchain.digest(path)
    except OSError:
        return 'missing'


def host():
    machine = platform.machine().lower()
    return platform.system().lower() + '-' + {'aarch64': 'arm64', 'amd64': 'x86_64'}.get(machine, machine)


def build_key(environ=os.environ):
    """A SHA-256 of what the build reads from outside the checkout (the toolchain by its location and pins: the
    build refuses any other bytes there) and of the interpreter that runs it."""
    pins = json.loads((ROOT / 'toolchains.json').read_text())['hosts'].get(host())
    key = dict(
        stock={name: file_sha(paths.STOCK_121 / name) for name in ('os.ldr', 'bootloader.ldr')},
        toolchain=dict(root=str(toolchain.location(environ).expanduser().resolve()), host=host(), pins=pins),
        python=sys.version,
        pyelftools=metadata.version('pyelftools'),
        checkout=str(ROOT),
    )
    return hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()


def turbo_command(task, force=False):
    """Local cache only (no remote, whatever TURBO_CACHE says); --force writes it without reading it."""
    return [
        str(TURBO),
        'run',
        TASKS[task],
        '--cache=local:' + ('w' if force else 'rw'),
        '--ui=stream',
        '--log-prefix=none',
    ]


def run_turbo(task, force=False):
    install = dev_tools.js_install(ROOT)  # a fresh worktree: Turborepo is a root devDependency
    if install and subprocess.run(install, cwd=ROOT).returncode:
        return 1
    env = dict(os.environ, **ENV, **{KEY: build_key()})
    return subprocess.run(turbo_command(task, force), cwd=ROOT, env=env).returncode


def run_task(task):
    """The task itself (turbo runs it on a miss): a fresh build into OUT, or --verify of OUT."""
    if task == 'build':
        shutil.rmtree(OUT, ignore_errors=True)  # xyf_build.py builds only into a fresh folder
        args = ['--xyf-product', '--out', OUT]
    else:
        args = ['--verify', '--out', OUT]
    return subprocess.run([sys.executable, str(ROOT / 'xyf_build.py'), *map(str, args)], cwd=ROOT).returncode


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] in (['run-build'], ['run-verify']) and len(argv) == 1:
        return run_task(argv[0][4:])
    if argv[:1] == ['key'] and len(argv) == 1:
        print(build_key())
        return 0
    if argv[:1] in (['build'], ['verify']) and argv[1:] in ([], ['--force']):
        return run_turbo(argv[0], force=argv[1:] == ['--force'])
    print(__doc__.split('\n\n')[1], file=sys.stderr)
    return 2


if __name__ == '__main__':
    sys.exit(main())
