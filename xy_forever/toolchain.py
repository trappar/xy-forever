"""The Blackfin toolchain in the build image (xy_forever/container.py): its location there and its authentication
against toolchains.json; no SDK imports or downloads."""

import hashlib
import json
import os
import platform
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IN_IMAGE = '/opt/opxy-toolchain/out/blackfin'  # docker/toolchain.Dockerfile (open-xy's build path)
PIN_SHA256 = '6d0e2aa9d58d2a49757d276c1cdee1b4eea32a2ae87888dfbfcdcdfc21a65f2e'
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
    """Where the toolchain is: XYF_TOOLCHAIN (the image sets it), else the image's folder. The host has none: a
    test that needs it skips there and runs in the container (the toolchain marker)."""
    return Path(environ.get('XYF_TOOLCHAIN') or IN_IMAGE)


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
    if not Path(found).is_dir():
        raise ValueError('No Blackfin toolchain at %s: firmware builds run in the container (container.py)' % found)
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
