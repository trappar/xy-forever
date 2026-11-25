"""The developer package `xy-forever` (#284; pypi/pyproject.toml): stage, build, check, and what to publish.

    uv run scripts/xyf_dist.py build [--version V] [--out DIR]       stage the package's inputs, then `uv build`
    uv run scripts/xyf_dist.py check DIR [--python PY]               the built wheel, installed and run (below)
    uv run scripts/xyf_dist.py plan --event EVENT [--before SHA]     version= and publish= lines (GITHUB_OUTPUT)
    uv run scripts/xyf_dist.py version [--dev]                       the next normal version (or this commit's dev one)

The package's inputs: pypi/ (its pyproject.toml, README.md), LICENSE and the modules pypi/pyproject.toml lists. build
copies them into a fresh folder (the version written into its pyproject.toml) and
runs `uv build` there: an sdist, then the wheel built from that sdist, so the sdist builds on its own.

check takes the wheel and sdist in DIR: the wheel holds exactly the listed files and stays small; a new virtual
environment (PY: the oldest supported Python, 3.11) installs it; `xyf --help` and `xyf --version` run; every module
imports; and `xyf logs` against a stand-in OP-XY whose HELLO names protocol 1.4 stops with the mismatch line and
exit code 2 (the simulator needs the private stock bootloader, which CI never has).

Versions (docs/operating/pypi.md): the package's own. A normal version is published by an XY Forever release when
the inputs changed since the last one (tag xyf-vX.Y.Z); every push to main that changes the inputs publishes
NEXT.devN, N the commit count of main (unique and increasing). NEXT: pypi/pyproject.toml's version, or the patch
after the last xyf-v tag when that one is not lower.
"""

import argparse
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / 'pypi'
TAG = 'xyf-v'
WHEEL_LIMIT = 150 * 1024  # bytes: the wheel is source only (about 90 KB)
ZERO = '0' * 40  # github.event.before of a new branch


def config():
    return tomllib.loads((PROJECT / 'pyproject.toml').read_text())


def modules():
    return config()['tool']['xyf']['modules']


def inputs():
    """Every path whose content goes into the package (relative to the repository)."""
    return ['pypi', 'LICENSE'] + ['xy_forever/%s.py' % m for m in modules()]


def git(*args):
    return subprocess.run(['git', *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


# -- versions -------------------------------------------------------------------------------------------
def parse(text):
    match = re.fullmatch(r'(\d+)\.(\d+)\.(\d+)', text)
    return tuple(int(p) for p in match.groups()) if match else None


def text(version):
    return '%d.%d.%d' % version


def released():
    """The normal versions published so far (their tags), oldest first."""
    found = [parse(tag[len(TAG) :]) for tag in git('tag', '--list', TAG + '*').split()]
    return sorted(v for v in found if v)


def next_version(floor, done):
    """The next normal version: floor (pyproject.toml's), unless the last published one is not lower."""
    if not done or floor > done[-1]:
        return floor
    return done[-1][:2] + (done[-1][2] + 1,)


def version(dev=False):
    nxt = text(next_version(parse(config()['project']['version']), released()))
    return '%s.dev%s' % (nxt, git('rev-list', '--count', 'HEAD')) if dev else nxt


def changed(since):
    """True when an input differs between since and HEAD (or since is unknown)."""
    if not since or since == ZERO:
        return True
    try:
        git('cat-file', '-e', since + '^{commit}')
    except subprocess.CalledProcessError:
        return True
    return subprocess.run(['git', 'diff', '--quiet', since, 'HEAD', '--', *inputs()], cwd=ROOT).returncode != 0


def plan(event, before=None):
    """{version, publish}: a release publishes the next normal version when the inputs changed since the last one; a
    push to main publishes a dev version when they changed since the push's previous commit; anything else builds a
    dev version and publishes nothing."""
    if event == 'release':
        done = released()
        return dict(version=version(), publish=changed(TAG + text(done[-1]) if done else None))
    return dict(version=version(dev=True), publish=event == 'push' and changed(before))


# -- build ----------------------------------------------------------------------------------------------
def stage(out, release=None):
    """The package's inputs in out, ready for `uv build` (release: the version written into pyproject.toml)."""
    out = Path(out)
    (out / 'xy_forever').mkdir(parents=True)
    project = (PROJECT / 'pyproject.toml').read_text()
    if release:
        project, n = re.subn(r'^version = "[^"]*"', 'version = "%s"' % release, project, count=1, flags=re.M)
        assert n == 1, 'pypi/pyproject.toml: no version line'
    (out / 'pyproject.toml').write_text(project)
    shutil.copy2(PROJECT / 'README.md', out / 'README.md')
    shutil.copy2(ROOT / 'LICENSE', out / 'LICENSE')
    for name in modules():
        shutil.copy2(ROOT / 'xy_forever' / (name + '.py'), out / 'xy_forever' / (name + '.py'))
    return out


def build(out, release=None):
    out = Path(out).resolve()
    with tempfile.TemporaryDirectory(prefix='xyf-dist-') as tmp:
        staged = stage(Path(tmp) / 'xy-forever', release)
        subprocess.run(['uv', 'build', '--quiet', '--out-dir', str(out), str(staged)], check=True)
    return sorted(out.glob('xy_forever-*'))


# -- check ----------------------------------------------------------------------------------------------
class StandIn:
    """A TCP endpoint that answers HELLO as an OP-XY with an older XY Forever would (protocol 1.4); --sim's form."""

    HELLO = 'XYF1;service=usb;role=b;proto=1.4;build=150;mode=product'

    def __init__(self):
        if str(ROOT) not in sys.path:  # run without the repository's environment (the Windows check)
            sys.path.insert(0, str(ROOT))
        from xy_forever import usb_proto as P  # the repository's: the frames the OP-XY sends

        self.P = P
        self.server = socket.create_server(('127.0.0.1', 0))
        self.address = '127.0.0.1:%d,1' % self.server.getsockname()[1]
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        while True:
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            threading.Thread(target=self.answer, args=(conn,), daemon=True).start()

    def answer(self, conn):
        P, parser = self.P, self.P.Parser()
        with conn:
            while data := conn.recv(4096):
                for kind, seq, _ in parser.feed(data):
                    if kind == P.HELLO:
                        conn.sendall(P.encode(P.HELLO | P.REPLY, seq, b'\0\0\0\0' + self.HELLO.encode()))


def run(argv, env=None):
    result = subprocess.run(argv, capture_output=True, text=True, env=env, timeout=60)
    return result.returncode, result.stdout + result.stderr


def check(folder, python='3.11'):
    """The built package, installed and run: [failures] (none: passed)."""
    folder = Path(folder)
    wheels, sdists = sorted(folder.glob('xy_forever-*.whl')), sorted(folder.glob('xy_forever-*.tar.gz'))
    if len(wheels) != 1 or len(sdists) != 1:
        return ['%s: want one wheel and one sdist, found %s' % (folder, [p.name for p in wheels + sdists])]
    wheel, failures = wheels[0], []
    with zipfile.ZipFile(wheel) as z:
        names = {n for n in z.namelist() if '.dist-info/' not in n}
    want = {'xy_forever/%s.py' % m for m in modules()}
    if names != want:
        failures.append('wheel files: extra %s, missing %s' % (sorted(names - want), sorted(want - names)))
    if wheel.stat().st_size > WHEEL_LIMIT:
        failures.append('wheel is %d bytes, over %d' % (wheel.stat().st_size, WHEEL_LIMIT))
    with tempfile.TemporaryDirectory(prefix='xyf-check-') as tmp:
        venv = Path(tmp) / 'venv'
        subprocess.run(['uv', 'venv', '--quiet', '--no-project', '--python', python, str(venv)], check=True)
        scripts = venv / ('Scripts' if os.name == 'nt' else 'bin')
        exe = '.exe' if os.name == 'nt' else ''
        python_bin, xyf = str(scripts / ('python' + exe)), str(scripts / ('xyf' + exe))
        subprocess.run(['uv', 'pip', 'install', '--quiet', '--python', python_bin, str(wheel)], check=True)
        env = dict(os.environ, XYF_NO_DEVICE='1')
        env.pop('XYF_SIM', None)
        code, out = run([xyf, '--help'], env)
        if code or not all(command in out for command in ('stock-os', 'dev', 'logs')):
            failures.append('xyf --help: exit %d\n%s' % (code, out))
        code, out = run([xyf, '--version'], env)
        if code or wheel.name.split('-')[1] not in out:
            failures.append('xyf --version: exit %d: %s' % (code, out))
        imports = 'import importlib\nfor m in %r: importlib.import_module("xy_forever." + m)' % modules()
        code, out = run([python_bin, '-I', '-c', imports], env)
        if code:
            failures.append('imports on %s: exit %d\n%s' % (python, code, out))
        stand_in = StandIn()
        env['XYF_SIM'] = stand_in.address
        code, out = run([xyf, 'logs', '--wait', '5', '--out', str(Path(tmp) / 'logs')], env)
        want = 'xyf logs: the OP-XY speaks XY Forever protocol 1.4; xyf needs 1.5 or a later 1.x. Update XY Forever'
        if code != 2 or want not in out:
            failures.append('the protocol mismatch: exit %d (want 2)\n%s' % (code, out))
    return failures


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    b = sub.add_parser('build')
    b.add_argument('--version', help='the version to build (default: the dev version of this commit)')
    b.add_argument('--out', type=Path, default=ROOT / 'build' / 'xyf-dist')
    c = sub.add_parser('check')
    c.add_argument('folder', type=Path)
    c.add_argument('--python', default='3.11', help='the Python to install it on (default 3.11, the oldest)')
    p = sub.add_parser('plan')
    p.add_argument('--event', required=True, help='github.event_name: push, release, pull_request')
    p.add_argument('--before', help='github.event.before (a push)')
    v = sub.add_parser('version')
    v.add_argument('--dev', action='store_true')
    args = parser.parse_args(argv)
    if args.command == 'build':
        for path in build(args.out, args.version or version(dev=True)):
            print('%s (%d bytes)' % (path, path.stat().st_size))
        return 0
    if args.command == 'check':
        failures = check(args.folder, args.python)
        print('\n'.join(failures) or 'package check: passed (%s on Python %s)' % (args.folder, args.python))
        return 1 if failures else 0
    if args.command == 'plan':
        result = plan(args.event, args.before)
        print('version=%s\npublish=%s' % (result['version'], str(result['publish']).lower()))
        return 0
    print(version(args.dev))
    return 0


if __name__ == '__main__':
    sys.exit(main())
