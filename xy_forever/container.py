"""The firmware build container (#321): every firmware build, and every test that needs the Blackfin toolchain, runs
in the image toolchains.json pins ("image": docker/toolchain.Dockerfile, published by toolchain-image.yml) on every
OS, so the bytes come from the image, never from the host that ran it.

    container.run(['xyf-build', '--xyf-mtp-usb', '--out', 'build/mtp-usb'])
    uv run -m xy_forever.container run xyf-build --xyf-mtp-usb --out build/mtp-usb     (the same, by hand)
    uv run -m xy_forever.container pull                                                 (pull the image, once)

Inside, the checkout is /src (the working directory: give paths relative to it), with its own venv
(build/container-venv/, synced from uv.lock on each run, so a run starts in about 0.3 s) and uv's download cache
(the named volume xyf-uv-cache). The private folder is /tmp/private: its firmware/ (the stock pair) mounted read
only when present, and `out`, a host folder, at /tmp/private/out when given. On Linux the container runs as the
host's user, so everything it writes is the user's. Docker pulls the image on first use: the package is private
until launch, so a pull logs in to ghcr.io with gh's token (docs/operating/dev-tools.md).
"""

import json
import os
import subprocess
import sys
from pathlib import Path

from . import paths

ROOT = Path(__file__).resolve().parents[1]
SRC = '/src'
PRIVATE = '/tmp/private'
OUT = PRIVATE + '/out'
CACHE = 'xyf-uv-cache'
REGISTRY = 'ghcr.io'
PASSED = ('XYF_NO_DEVICE',)  # the environment a run inside keeps from the host (the rest is the image's)
SYNC = 'uv sync --locked --no-group native --quiet && exec uv run --no-sync "$@"'
LOGIN = (
    'Docker could not pull the build image %s. It is private until launch: give gh the read:packages scope '
    '(`gh auth refresh -h github.com -s read:packages`), then run the command again (it logs Docker in with '
    "gh's token), or `docker login ghcr.io` yourself."
)


class ContainerError(Exception):
    """Docker is missing, not running, or cannot pull the image (the message says which and what to do)."""


def image():
    """The pinned image: name@sha256:digest (this checkout's toolchains.json, whatever tree a run builds)."""
    return json.loads((ROOT / 'toolchains.json').read_text())['image']


def command(argv, root=ROOT, out=None, environ=os.environ):
    """The docker command that runs argv (through `uv run`) in the container, root at /src."""
    root = Path(root).resolve()
    venv = root / 'build' / 'container-venv'
    venv.mkdir(parents=True, exist_ok=True)  # made here, so it is the user's (Docker would make it root's)
    cmd = ['docker', 'run', '--rm']
    if sys.platform == 'linux':  # Docker Desktop (macOS, Windows) maps ownership itself
        cmd += ['--user', '%d:%d' % (os.getuid(), os.getgid())]
    cmd += ['-v', '%s:%s' % (root, SRC), '-v', '%s:/opt/venv' % venv, '-v', '%s:/opt/uv-cache' % CACHE]
    if paths.FIRMWARE.is_dir():
        cmd += ['-v', '%s:%s/firmware:ro' % (paths.FIRMWARE, PRIVATE)]
    if out is not None:
        Path(out).mkdir(parents=True, exist_ok=True)
        cmd += ['-v', '%s:%s' % (Path(out).resolve(), OUT)]
    cmd += ['-e', 'XYF_PRIVATE=' + PRIVATE]
    for k in PASSED:
        cmd += ['-e', '%s=%s' % (k, environ[k])] if k in environ else []
    return cmd + [image(), 'sh', '-c', SYNC, 'sh', *map(str, argv)]


def docker(*args, **kw):
    try:
        return subprocess.run(['docker', *args], **kw)
    except FileNotFoundError:
        raise ContainerError(
            'Docker is needed for firmware builds and is not installed (docs/operating/dev-tools.md: per OS)'
        )


def problem():
    """None when Docker runs and has the pinned image, else why not (doctor: nothing is pulled)."""
    try:
        have = docker('image', 'inspect', image(), capture_output=True, text=True)
    except ContainerError as e:
        return str(e)
    if have.returncode == 0:
        return None
    if 'no such image' in have.stderr.lower():
        return 'not pulled yet (mise run setup pulls it)'
    return 'Docker is not running: start it'


def ensure_image():
    """Pull the pinned image unless Docker has it; a refused pull logs in to ghcr.io with gh's token, once."""
    name = image()
    have = docker('image', 'inspect', name, capture_output=True, text=True)
    if have.returncode == 0:
        return
    if 'no such image' not in have.stderr.lower():
        raise ContainerError('Docker is not running (%s): start it' % (have.stderr.strip() or 'no answer'))
    if docker('pull', '--quiet', name, capture_output=True).returncode == 0:
        return
    if not gh_login() or docker('pull', '--quiet', name, capture_output=True).returncode:
        raise ContainerError(LOGIN % name)


def gh_login():
    """docker login ghcr.io with gh's token; False when gh has none."""
    try:
        token = subprocess.run(['gh', 'auth', 'token'], capture_output=True, text=True).stdout.strip()
        user = subprocess.run(['gh', 'api', 'user', '--jq', '.login'], capture_output=True, text=True).stdout.strip()
    except FileNotFoundError:
        return False
    if not (token and user):
        return False
    login = docker('login', REGISTRY, '-u', user, '--password-stdin', input=token, capture_output=True, text=True)
    return login.returncode == 0


def run(argv, root=ROOT, out=None, **kw):
    """Run argv in the container (subprocess.run's keywords: capture_output, text, ...)."""
    ensure_image()
    return subprocess.run(command(argv, root, out), **kw)


BATCH, RESULTS = '.blackfin-batch.json', '.blackfin-results.json'


def blackfin(commands, work, root=ROOT):
    """Blackfin tool commands, all in one container run (the tests that compile a unit and read what it made): each
    [tool, *args], tool a bin/ name without its bfin-elf- prefix (g++, as, objdump, nm). work, a host folder, is
    the working directory; an argument under work or root names a host path and is mapped to the container's.
    Returns [(returncode, stdout, stderr)], one per command."""
    work = Path(work).resolve()

    def inside(arg):
        arg = os.path.realpath(arg) if os.path.isabs(str(arg)) else str(arg)
        for host, there in ((str(work), OUT), (str(Path(root).resolve()), SRC)):
            if arg == host or arg.startswith(host + os.sep):
                return there + arg[len(host) :].replace(os.sep, '/')
        return arg

    (work / BATCH).write_text(json.dumps([[tool, *map(inside, args)] for tool, *args in commands]))
    argv = ['python', '-m', 'xy_forever.container', 'batch', OUT + '/' + BATCH]
    done = run(argv, root, work, capture_output=True, text=True)
    if done.returncode:
        raise ContainerError('the container run failed: ' + (done.stderr.strip() or 'exit %d' % done.returncode))
    return [tuple(r) for r in json.loads((work / RESULTS).read_text())]


def main(argv):
    """run ARGV... and pull on the host; batch FILE inside the container (blackfin())."""
    if argv[:1] == ['run'] and argv[1:]:
        return run(argv[1:]).returncode
    if argv == ['pull']:
        ensure_image()
        return 0
    if argv[:1] == ['batch'] and len(argv) == 2:
        return batch(Path(argv[1]))
    print(__doc__.split('\n\n')[1], file=sys.stderr)
    return 2


def batch(path):
    """Inside the container: run a blackfin() batch, in its folder, and write the results beside it."""
    from . import toolchain

    batch = path
    tools, results = toolchain.location() / 'bin', []
    for tool, *args in json.loads(batch.read_text()):
        done = subprocess.run(
            [str(tools / ('bfin-elf-' + tool)), *args], cwd=batch.parent, capture_output=True, text=True
        )
        results.append((done.returncode, done.stdout, done.stderr))
    (batch.parent / RESULTS).write_text(json.dumps(results))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv[1:]))
    except ContainerError as e:
        sys.exit('container: %s' % e)
