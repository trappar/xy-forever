"""Two agents on one machine (docs/operating/two-agents.md): paths from the main checkout in a worktree and a plain
clone, the device lock. Every case also runs with nothing set up: no xyf.local.json, no XYF_*
variables."""

import contextlib
import importlib.util
import io
import json
import os
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pytest

from xy_forever import device_lock, paths

ROOT = Path(__file__).resolve().parents[1]


def script(name):
    spec = importlib.util.spec_from_file_location(name + '_script', ROOT / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SETUP = script('setup')
CLEAN_ENV = {k: v for k, v in os.environ.items() if not k.startswith('XYF_') or k == 'XYF_NO_DEVICE'}
SHOW = (
    'from xy_forever import paths; import json; print(json.dumps({k: str(v) for k, v in dict('
    'main=paths.MAIN, config=paths.LOCAL_CONFIG, private=paths.PRIVATE_ROOT, status=paths.STATUS).items()}))'
)


def git(cwd, *args):
    return subprocess.run(
        ['git', '-c', 'user.name=t', '-c', 'user.email=t@example.com', *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.mark.gate
class Workspace(unittest.TestCase):
    """ws/xy-forever: a plain clone holding a copy of xy_forever/ (paths.py); elsewhere/wt: a worktree
    of it outside the workspace."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name).resolve()
        self.ws, self.repo, self.wt = base / 'ws', base / 'ws/xy-forever', base / 'elsewhere/wt'
        shutil.copytree(
            ROOT / 'xy_forever',
            self.repo / 'xy_forever',
            ignore=lambda d, names: [n for n in names if n != '__init__.py' and not n.endswith('.py')],
        )
        git(self.repo, 'init', '-q', '-b', 'main')
        git(self.repo, 'add', '-A')
        git(self.repo, 'commit', '-q', '-m', 'base')
        git(self.repo, 'worktree', 'add', '-q', '-b', '7-item', str(self.wt))

    def show(self, where, env=None):
        run = subprocess.run(
            [sys.executable, '-c', SHOW],
            cwd=str(where),
            capture_output=True,
            text=True,
            env=dict(CLEAN_ENV, **(env or {})),
            check=True,
        )
        return {k: Path(v) for k, v in json.loads(run.stdout).items()}

    def test_a_plain_clone_resolves_as_before(self):
        got = self.show(self.repo)
        self.assertEqual(
            got,
            dict(
                main=self.repo,
                config=self.repo / 'xyf.local.json',
                private=self.ws / 'private',
                status=self.ws / 'private/xy-forever/status',
            ),
        )
        self.assertEqual(paths.main_checkout(self.repo), self.repo)
        self.assertEqual(paths.main_checkout(self.ws), self.ws)  # no git at all

    def test_a_worktree_resolves_from_the_main_checkout(self):
        self.assertEqual(self.show(self.wt), self.show(self.repo))
        self.assertEqual(paths.main_checkout(self.wt), self.repo)

    def test_the_main_checkouts_config_and_the_variables_still_win(self):
        (self.repo / 'xyf.local.json').write_text(json.dumps({'private': str(self.ws / 'mine')}))
        self.assertEqual(self.show(self.wt)['private'], self.ws / 'mine')
        self.assertEqual(self.show(self.wt, {'XYF_PRIVATE': str(self.ws / 'env')})['private'], self.ws / 'env')
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


HOLD = (
    'import sys, time; from xy_forever import device_lock; '
    'device_lock.hold(float(sys.argv[3]), sys.argv[1], sys.argv[2]); print("held", flush=True); '
    'time.sleep(float(sys.argv[4]))'
)
BETWEEN_COMMANDS = """
import subprocess, sys, time
from pathlib import Path
release, *command = sys.argv[1:]
subprocess.run(command, stdout=subprocess.DEVNULL, check=True)
print('between commands', flush=True)
while not Path(release).exists():
    time.sleep(0.02)
subprocess.run(command, check=True)
"""
PARALLEL_COMMANDS = r"""
import subprocess, sys
*command, seconds = sys.argv[1:]
with subprocess.Popen(command + [seconds], stdout=subprocess.PIPE, text=True) as first:
    try:
        assert first.stdout.readline() == 'held\n'
        second = subprocess.run(command + ['0'], capture_output=True, text=True)
        assert second.returncode == 1, second
        assert 'pid %d' % first.pid in second.stderr, second
    finally:
        first.terminate()
detached = subprocess.run(command + ['0'], capture_output=True, text=True, start_new_session=True)
assert detached.returncode == 1, detached
assert 'device session ended' in detached.stderr, detached
subprocess.run(command + ['0'], check=True)
"""


@pytest.mark.gate
class DeviceLock(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.lock = self.root / 'xy-forever/device.lock'

    def holder(self, seconds=30):
        proc = subprocess.Popen(
            [sys.executable, '-c', HOLD, str(self.lock), str(self.root), '0', str(seconds)],
            stdout=subprocess.PIPE,
            text=True,
        )
        self.addCleanup(proc.stdout.close)
        self.addCleanup(lambda: (proc.poll() is None and proc.kill()) or proc.wait())
        self.assertEqual(proc.stdout.readline().strip(), 'held')
        return proc

    def second(self, wait=0.0):
        return subprocess.run(
            [sys.executable, '-c', HOLD, str(self.lock), str(self.root), str(wait), '0'], capture_output=True, text=True
        )

    def session(self, *command, timeout=10, wait=0):
        return [
            sys.executable,
            '-m',
            'xy_forever.device_session',
            '--name',
            'host qualification',
            '--wait',
            str(wait),
            '--timeout',
            str(timeout),
            '--',
            *command,
        ]

    def start_session(self, *command, timeout=10, ready='held'):
        proc = subprocess.Popen(
            self.session(*command, timeout=timeout),
            env=dict(CLEAN_ENV, XYF_PRIVATE=str(self.root)),
            stdout=subprocess.PIPE,
            text=True,
        )
        self.addCleanup(proc.stdout.close)
        self.addCleanup(lambda: (proc.poll() is None and proc.kill()) or proc.wait())
        self.assertTrue(select.select([proc.stdout], [], [], 5)[0], 'session did not start')
        self.assertEqual(proc.stdout.readline().strip(), ready)
        return proc

    def test_lease_excludes_other_tools_and_sessions_between_commands(self):
        release = self.root / 'resume'
        cmd = [sys.executable, '-c', HOLD, str(self.lock), str(self.root), '0', '0']
        proc = self.start_session(sys.executable, '-c', BETWEEN_COMMANDS, str(release), *cmd, ready='between commands')
        got = self.second()
        self.assertEqual(got.returncode, 1)
        self.assertIn('pid %d (lease: host qualification) since ' % proc.pid, got.stderr)
        competing = subprocess.run(
            self.session(*cmd, wait=0.1),
            env=dict(CLEAN_ENV, XYF_PRIVATE=str(self.root)),
            capture_output=True,
            text=True,
        )
        self.assertEqual(competing.returncode, 1)
        self.assertIn('waiting up to 0.1 s', competing.stderr)
        release.touch()
        self.assertEqual((proc.wait(timeout=5), proc.stdout.read().strip()), (0, 'held'))
        self.assertEqual(self.second().returncode, 0)

    def test_only_one_command_at_a_time_under_the_lease(self):
        cmd = [sys.executable, '-c', HOLD, str(self.lock), str(self.root), '0', '30']
        proc = self.start_session(sys.executable, '-c', PARALLEL_COMMANDS, *cmd)
        self.assertEqual(proc.wait(timeout=5), 0)

    def test_lease_expiry_ends_the_command_and_releases_access(self):
        cmd = [sys.executable, '-c', HOLD, str(self.lock), str(self.root), '0', '30']
        proc = self.start_session(*cmd, timeout=1)
        self.assertEqual(proc.wait(timeout=6), 124)
        self.assertEqual(self.second().returncode, 0)

    def test_killing_the_lease_owner_ends_its_in_flight_command(self):
        cmd = [sys.executable, '-c', HOLD, str(self.lock), str(self.root), '0', '30']
        proc = self.start_session(*cmd)
        proc.kill()
        proc.wait(timeout=5)
        self.assertEqual(self.second(wait=3).returncode, 0)

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
            device_lock.release(self.lock)

    def test_no_lock_without_a_private_folder_or_in_a_test_run(self):
        self.assertIsNone(device_lock.hold(path=self.lock, root=self.root / 'absent'))
        self.assertFalse(self.lock.exists())
        with mock.patch.object(paths, 'DEVICE_LOCK', self.lock), mock.patch.object(paths, 'PRIVATE_ROOT', self.root):
            self.assertIsNone(device_lock.hold())  # XYF_NO_DEVICE: a test takes no real lock
        self.assertFalse(self.lock.exists())

    def test_the_simulator_takes_no_lock(self):
        from xy_forever import xyf_host

        with (
            mock.patch.object(xyf_host, 'SIM', '127.0.0.1:1,2'),
            mock.patch.object(xyf_host, 'find_port', lambda: None),
            mock.patch.object(device_lock, 'hold') as hold,
            contextlib.redirect_stderr(io.StringIO()),
            self.assertRaisesRegex(SystemExit, 'no simulator control port at 127.0.0.1:2'),  # never a wall-clock wait
        ):
            xyf_host.connect(0.05)
        hold.assert_not_called()
        with (
            mock.patch.object(xyf_host, 'SIM', None),
            mock.patch.object(xyf_host, 'find_port', lambda: None),
            mock.patch.object(device_lock, 'hold') as hold,
            contextlib.redirect_stderr(io.StringIO()),
            self.assertRaisesRegex(SystemExit, 'not found'),
        ):
            xyf_host.connect(0.05)
        hold.assert_called_once_with(0.05)


if __name__ == '__main__':
    unittest.main()
