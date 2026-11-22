"""Two agents on one machine (docs/operating/two-agents.md): paths from the main checkout in a worktree and a plain
clone, the device lock, safe build pruning. Every case also runs with nothing set up: no xyf.local.json, no XYF_*
variables."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

import pytest

from xy_forever import device_lock, paths

ROOT = Path(__file__).resolve().parents[1]


def script(name):
    spec = importlib.util.spec_from_file_location(name + '_script', ROOT / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CHECK, SETUP = script('check'), script('setup')
CLEAN_ENV = {k: v for k, v in os.environ.items() if not k.startswith('XYF_') or k == 'XYF_NO_DEVICE'}
SHOW = ('from xy_forever import paths, toolchain; import json; print(json.dumps({k: str(v) for k, v in dict('
        'main=paths.MAIN, config=paths.LOCAL_CONFIG, private=paths.PRIVATE_ROOT, toolchain=toolchain.location(), '
        'status=paths.STATUS).items()}))')


def git(cwd, *args):
    return subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@example.com', *args], cwd=str(cwd),
                          check=True, capture_output=True, text=True).stdout.strip()


@pytest.mark.gate
class Workspace(unittest.TestCase):
    """ws/xy-forever: a plain clone holding a copy of xy_forever/ (paths.py, toolchain.py); elsewhere/wt: a worktree
    of it outside the workspace."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name).resolve()
        self.ws, self.repo, self.wt = base / 'ws', base / 'ws/xy-forever', base / 'elsewhere/wt'
        shutil.copytree(ROOT / 'xy_forever', self.repo / 'xy_forever',
                        ignore=lambda d, names: [n for n in names if n != '__init__.py' and not n.endswith('.py')])
        shutil.copy(ROOT / 'toolchains.json', self.repo)
        git(self.repo, 'init', '-q', '-b', 'main')
        git(self.repo, 'add', '-A')
        git(self.repo, 'commit', '-q', '-m', 'base')
        git(self.repo, 'worktree', 'add', '-q', '-b', '7-item', str(self.wt))

    def show(self, where, env=None):
        run = subprocess.run([sys.executable, '-c', SHOW], cwd=str(where), capture_output=True, text=True,
                             env=dict(CLEAN_ENV, **(env or {})), check=True)
        return {k: Path(v) for k, v in json.loads(run.stdout).items()}

    def test_a_plain_clone_resolves_as_before(self):
        got = self.show(self.repo)
        self.assertEqual(got, dict(main=self.repo, config=self.repo / 'xyf.local.json', private=self.ws / 'private',
                                   toolchain=self.ws / 'tools/blackfin', status=self.ws / 'private/xy-forever/status'))
        self.assertEqual(paths.main_checkout(self.repo), self.repo)
        self.assertEqual(paths.main_checkout(self.ws), self.ws)            # no git at all

    def test_a_worktree_resolves_from_the_main_checkout(self):
        self.assertEqual(self.show(self.wt), self.show(self.repo))
        self.assertEqual(paths.main_checkout(self.wt), self.repo)

    def test_the_main_checkouts_config_and_the_variables_still_win(self):
        (self.repo / 'xyf.local.json').write_text(json.dumps({'private': str(self.ws / 'mine')}))
        self.assertEqual(self.show(self.wt)['private'], self.ws / 'mine')
        got = self.show(self.wt, {'XYF_PRIVATE': str(self.ws / 'env'), 'XYF_TOOLCHAIN': str(self.ws / 'tc')})
        self.assertEqual((got['private'], got['toolchain']), (self.ws / 'env', self.ws / 'tc'))
        got = self.show(self.repo, {'XYF_PRIVATE': str(self.ws / 'env')})
        self.assertEqual(got['private'], self.ws / 'env')

    def test_bootstrap_in_a_worktree_writes_the_main_checkouts_files(self):
        (self.wt / 'docs').mkdir()
        (self.wt / 'docs/STATUS.template.md').write_text('# status\n')
        b = SETUP.Bootstrap(repo=self.wt, dry_run=True, environ={})
        self.assertEqual((b.main, b.workspace, b.config), (self.repo, self.ws, self.repo / 'xyf.local.json'))
        with contextlib.redirect_stdout(io.StringIO()):
            b.private()
            b.local_files()
        self.assertFalse((self.ws / 'private').exists())
        b.dry_run = False
        with contextlib.redirect_stdout(io.StringIO()):
            b.private()
            b.local_files()
        self.assertEqual((self.ws / 'private/xy-forever/status/README.md').read_text(), '# status\n')
        plain = SETUP.Bootstrap(repo=self.repo, environ={})
        self.assertEqual((plain.main, plain.workspace), (self.repo, self.ws))



HOLD = ('import sys, time; from xy_forever import device_lock; '
        'device_lock.hold(float(sys.argv[3]), sys.argv[1], sys.argv[2]); print("held", flush=True); '
        'time.sleep(float(sys.argv[4]))')


@pytest.mark.gate
class DeviceLock(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.lock = self.root / 'xy-forever/device.lock'

    def holder(self, seconds=30):
        proc = subprocess.Popen([sys.executable, '-c', HOLD, str(self.lock), str(self.root), '0', str(seconds)],
                                stdout=subprocess.PIPE, text=True)
        self.addCleanup(proc.stdout.close)
        self.addCleanup(lambda: proc.poll() is None and proc.kill() or proc.wait())
        self.assertEqual(proc.stdout.readline().strip(), 'held')
        return proc

    def second(self, wait=0.0):
        return subprocess.run([sys.executable, '-c', HOLD, str(self.lock), str(self.root), str(wait), '0'],
                              capture_output=True, text=True)

    def test_a_second_process_is_told_the_device_is_busy(self):
        proc = self.holder()
        got = self.second()
        self.assertEqual(got.returncode, 1)
        self.assertRegex(got.stderr, r'device busy: pid %d \(-c .*\) since .* is talking to the OP-XY' % proc.pid)
        got = self.second(0.2)
        self.assertIn('waiting up to 0.2 s', got.stderr)
        self.assertEqual(got.returncode, 1)

    def test_the_lock_goes_with_the_process_even_on_a_kill(self):
        proc = self.holder()
        proc.send_signal(signal.SIGKILL)
        proc.wait()
        got = self.second()
        self.assertEqual((got.returncode, got.stdout.strip()), (0, 'held'))

    def test_held_once_per_process(self):
        with mock.patch.dict(device_lock._held, clear=True):
            self.assertEqual(device_lock.hold(path=self.lock, root=self.root), str(self.lock))
            self.assertEqual(device_lock.hold(path=self.lock, root=self.root), str(self.lock))
            self.assertIn('pid %d' % os.getpid(), self.lock.read_text())
            device_lock._held[str(self.lock)].close()

    def test_no_lock_without_a_private_folder_or_in_a_test_run(self):
        self.assertIsNone(device_lock.hold(path=self.lock, root=self.root / 'absent'))
        self.assertFalse(self.lock.exists())
        with mock.patch.object(paths, 'DEVICE_LOCK', self.lock), mock.patch.object(paths, 'PRIVATE_ROOT', self.root):
            self.assertIsNone(device_lock.hold())                     # XYF_NO_DEVICE: a test takes no real lock
        self.assertFalse(self.lock.exists())

    def test_the_simulator_takes_no_lock(self):
        from xy_forever import xyf_host
        with mock.patch.object(xyf_host, 'SIM', '127.0.0.1:1'), mock.patch.object(xyf_host, 'find_port', lambda: None), \
                mock.patch.object(device_lock, 'hold') as hold, contextlib.redirect_stderr(io.StringIO()), \
                self.assertRaisesRegex(SystemExit, 'not found'):
            xyf_host.connect(0.05)
        hold.assert_not_called()
        with mock.patch.object(xyf_host, 'SIM', None), mock.patch.object(xyf_host, 'find_port', lambda: None), \
                mock.patch.object(device_lock, 'hold') as hold, contextlib.redirect_stderr(io.StringIO()), \
                self.assertRaisesRegex(SystemExit, 'not found'):
            xyf_host.connect(0.05)
        hold.assert_called_once_with(0.05)


@pytest.mark.gate
class Pruning(unittest.TestCase):
    def test_recent_folders_stay(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(CHECK.paths, 'BUILDS', Path(tmp)):
            builds, now = Path(tmp), time.time()
            for name in ('check-aaaaaaaaaaaa', 'check-bbbbbbbbbbbb', 'check-cccccccccccc'):
                (builds / name).mkdir()
                (builds / name / 'part.o').write_bytes(b'x')
            images = ['%08x' % n for n in range(7)]
            for n, image in enumerate(images):
                (builds / image).mkdir()
                (builds / image / 'check.json').write_text('{}')
            old = now - CHECK.RECENT - 60
            for path in builds.rglob('*'):
                os.utime(path, (old, old))
            for n, image in enumerate(images):                           # newest last: check.json order
                os.utime(builds / image / 'check.json', (old + n, old + n))
            os.utime(builds / images[1], (now, now))                     # another run is reading it
            os.utime(builds / 'check-bbbbbbbbbbbb' / 'part.o', (now, now))   # another run is building it
            CHECK.prune_builds(now)
            self.assertEqual(sorted(p.name for p in builds.iterdir()),
                             [images[1]] + images[3:] + ['check-bbbbbbbbbbbb'])


if __name__ == '__main__':
    unittest.main()
