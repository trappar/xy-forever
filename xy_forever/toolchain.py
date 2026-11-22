"""Standalone compiler/binutils authentication; no SDK imports or downloads."""

import hashlib
import json
import os
import platform
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIN_SHA256 = '81b2900fce63fe9b8871f72f4d9a9388ff7ea390a3795866d53ba1e22c2c9856'
CXX_FLAGS = [
    '-mcpu=bf707',
    '-O2',
    '-std=gnu++11',
    '-ffreestanding',
    '-fno-exceptions',
    '-fno-rtti',
    '-fno-threadsafe-statics',
    '-fno-use-cxa-atexit',
    '-fno-asynchronous-unwind-tables',
    '-fno-unwind-tables',
    '-fno-common',
    '-fno-tree-loop-distribute-patterns',
    '-fomit-frame-pointer',
    '-nostdinc++',
    '-Wall',
    '-Werror',
    '-Wno-unused-variable',
]


def digest(path):
    hasher = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            hasher.update(chunk)
    return hasher.hexdigest()


def location(environ=os.environ):
    """Where the toolchain is looked for: XYF_TOOLCHAIN, else paths.toolchain() (xyf.local.json's "toolchain", else
    tools/blackfin beside the main checkout: ../tools/blackfin in a plain clone, the same folder from any worktree)."""
    from . import paths

    return Path(environ['XYF_TOOLCHAIN']) if environ.get('XYF_TOOLCHAIN') else paths.toolchain()


def authenticate(directory=None):
    raw = (ROOT / 'toolchains.json').read_bytes()
    if hashlib.sha256(raw).hexdigest() != PIN_SHA256:
        raise ValueError('Reviewed toolchain pins changed')
    records = json.loads(raw)
    system = platform.system().lower()
    machine = platform.machine().lower()
    machine = {'aarch64': 'arm64', 'amd64': 'x86_64'}.get(machine, machine)
    host = system + '-' + machine
    if records.get('schema') != 1 or host not in records['hosts']:
        raise ValueError('No authenticated toolchain for ' + host)
    profile = records['hosts'][host]
    found = directory or location()
    if not directory and not os.environ.get('XYF_TOOLCHAIN') and not found.is_dir():
        raise ValueError(
            'No Blackfin toolchain at %s: `mise run setup` installs it there; for another folder, '
            '`uv run scripts/setup.py init --toolchain DIR` (or XYF_TOOLCHAIN)' % found
        )
    base = Path(found).expanduser().resolve()
    checked = {}

    def check(relative, expected):
        path = (base / relative).resolve()
        if not path.is_relative_to(base) or not path.is_file() or path.stat().st_size > 64 * 1024 * 1024:
            raise ValueError('Missing/oversized compiler input: ' + relative)
        if digest(path) != expected:
            raise ValueError('Toolchain hash mismatch: ' + relative)
        checked[relative] = expected
        return path

    suffix = profile['executable_suffix']
    compiler = {name: check(name, expected) for name, expected in profile['compiler'].items()}
    tools = {name: check('bin/bfin-elf-' + name + suffix, expected) for name, expected in profile['tools'].items()}
    return {'cxx': compiler['bin/bfin-elf-g++' + suffix], 'checked': checked, 'root': base}, tools
