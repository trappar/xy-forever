"""Two agents on one machine (docs/operating/two-agents.md): paths from the main checkout in a worktree and a plain
clone, local claims and plan.py --agent, the device lock, safe build pruning. Every case also runs with nothing set
up: no claims folder, no agent name, no xyf.local.json, no XYF_* variables."""
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from xy_forever import claims as C, device_lock, paths  # noqa: E402


def script(name):
    spec = importlib.util.spec_from_file_location(name + '_script', ROOT / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PLAN, CHECK, SETUP = script('plan'), script('check'), script('setup')
CLEAN_ENV = {k: v for k, v in os.environ.items() if not k.startswith('XYF_') or k == 'XYF_NO_DEVICE'}
SHOW = ('from xy_forever import paths, toolchain; import json; print(json.dumps({k: str(v) for k, v in dict('
        'main=paths.MAIN, config=paths.LOCAL_CONFIG, private=paths.PRIVATE_ROOT, toolchain=toolchain.location(), '
        'status=paths.STATUS, claims=paths.CLAIMS).items()}))')


def git(cwd, *args):
    return subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@example.com', *args], cwd=str(cwd),
                          check=True, capture_output=True, text=True).stdout.strip()


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
                                   toolchain=self.ws / 'tools/blackfin', status=self.repo / 'docs/status',
                                   claims=self.ws / 'private/xy-forever/claims'))
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
            b.local_files()
        b.dry_run = False
        with contextlib.redirect_stdout(io.StringIO()):
            b.local_files()
        self.assertEqual((self.repo / 'docs/status/README.md').read_text(), '# status\n')
        self.assertFalse((self.wt / 'docs/status').exists())
        plain = SETUP.Bootstrap(repo=self.repo, environ={})
        self.assertEqual((plain.main, plain.workspace), (self.repo, self.ws))

    def test_a_claim_records_the_worktree_and_its_branch(self):
        folder = self.ws / 'private/xy-forever/claims'
        got = C.claim(7, 'codex', root=self.wt, folder=folder, stamp='2026-01-01T00:00:00Z')
        self.assertEqual(got, dict(version=1, issue=7, agent='codex', branch='7-item', worktree=str(self.wt),
                                   time='2026-01-01T00:00:00Z'))
        self.assertEqual(json.loads((folder / '7.json').read_text()), got)


class Claims(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name) / 'claims'

    def test_no_claims_folder_is_no_claims(self):
        self.assertEqual(C.load(self.folder), {})
        self.assertIsNone(C.release(3, folder=self.folder))
        self.assertFalse(self.folder.exists())

    def test_the_second_claimant_is_refused_by_name(self):
        first = C.claim(5, 'claude', folder=self.folder)
        self.assertEqual(C.claim(5, 'claude', folder=self.folder), first)          # the same agent, again
        with self.assertRaisesRegex(C.ClaimError, r'#5 is claimed by claude .*leave it'):
            C.claim(5, 'codex', folder=self.folder)
        self.assertEqual(C.load(self.folder)[5]['agent'], 'claude')

    def test_names_come_from_the_fixed_list(self):
        with self.assertRaisesRegex(C.ClaimError, 'not one of the agent names'):
            C.claim(5, 'Claude', folder=self.folder)
        doc = (ROOT / 'docs/operating/two-agents.md').read_text()
        self.assertIn('fixed list: %s.' % ', '.join('`%s`' % a for a in C.AGENTS), doc)

    def test_release_only_by_its_agent_unless_unnamed(self):
        C.claim(5, 'claude', folder=self.folder)
        with self.assertRaisesRegex(C.ClaimError, 'only that agent releases it'):
            C.release(5, 'codex', folder=self.folder)
        self.assertEqual(C.release(5, 'claude', folder=self.folder)['agent'], 'claude')
        self.assertEqual(C.load(self.folder), {})

    def test_a_newer_or_broken_claim_is_refused_with_a_message(self):
        self.folder.mkdir()
        (self.folder / '5.json').write_text(json.dumps({'version': 2, 'agent': 'claude'}))
        with self.assertRaisesRegex(C.ClaimError, 'version 2; this plan.py reads 1'):
            C.load(self.folder)
        (self.folder / '5.json').write_text('{')
        with self.assertRaisesRegex(C.ClaimError, 'unreadable'):
            C.claim(5, 'codex', folder=self.folder)

    def test_merged_or_closed_pull_requests_release_the_claim(self):
        for n, branch in ((1, 'a'), (2, 'b'), (3, 'c'), (4, '')):
            C.claim(n, 'claude', folder=self.folder)
            data = json.loads((self.folder / ('%d.json' % n)).read_text())
            (self.folder / ('%d.json' % n)).write_text(json.dumps(dict(data, branch=branch)))
        states = {'a': {'MERGED'}, 'b': {'CLOSED', 'OPEN'}, 'c': set()}.get
        held = C.load(self.folder)
        gone = C.sweep(held, states, self.folder)
        self.assertEqual([n for n, _ in gone], [1])
        self.assertEqual(sorted(held), [2, 3, 4])
        self.assertEqual(sorted(C.load(self.folder)), [2, 3, 4])

    def test_stale_after_two_days_without_a_commit(self):
        claim = dict(branch='x', time='2026-01-01T00:00:00Z')
        t0 = C.epoch(claim['time'])
        self.assertFalse(C.stale(claim, lambda b: None, t0 + C.STALE - 1))
        self.assertTrue(C.stale(claim, lambda b: None, t0 + C.STALE + 1))
        self.assertFalse(C.stale(claim, lambda b: t0 + 100, t0 + C.STALE + 1))   # a newer commit of its own


def issue(number, title, labels=('kind:build',), assignees=()):
    return {'number': number, 'title': title, 'labels': [{'name': l} for l in labels],
            'assignees': [{'login': a} for a in assignees], 'milestone': {'number': 1, 'title': 'M0. Reset'},
            'issue_dependencies_summary': {'blocked_by': 0}}


ISSUES = [issue(1, 'Mine claimed', assignees=['me']), issue(2, 'Theirs', assignees=['me']),
          issue(3, 'Interrupted', assignees=['me']), issue(4, 'Free'), issue(5, 'Free, theirs')]
HELD = {1: dict(agent='claude', branch='1-mine'), 2: dict(agent='codex', branch='2-x'),
        5: dict(agent='codex', branch='5-y')}


class PlanByClaim(unittest.TestCase):
    def run_main(self, argv, folder):
        out = io.StringIO()
        with mock.patch.object(PLAN, 'fetch', lambda: (ISSUES, 'me')), mock.patch.object(C.paths, 'CLAIMS', folder), \
                contextlib.redirect_stdout(out):
            code = PLAN.main(argv)
        return code, out.getvalue()

    def test_with_agent_the_items_split_by_claim(self):
        out = PLAN.queue(ISSUES, 'me', agent='claude', held=HELD, stale=lambda c: c['branch'] == '2-x')
        self.assertEqual(out, [
            'Yours, in progress (claimed by claude) (1)',
            '  #1 M0 Mine claimed [build] (1-mine)',
            'Yours, unclaimed: interrupted, claimable (1)',
            '  #3 M0 Interrupted [build]',
            'Claimed by another agent: leave these (2)',
            '  #2 M0 Theirs [build] (codex, stale: no commits for 2 days)',
            '  #5 M0 Free, theirs [build] (codex)',
            'Next: unblocked, unassigned (1)',
            '  #4 M0 Free [build]',
            'Decisions waiting for me (0)',
            'Not shown: 0 blocked, 0 taken by others, 0 threads (--all)'])

    def test_without_agent_the_output_is_todays(self):
        today = ['Yours, in progress (3)', '  #1 M0 Mine claimed [build]', '  #2 M0 Theirs [build]',
                 '  #3 M0 Interrupted [build]', 'Next: unblocked, unassigned (2)', '  #4 M0 Free [build]',
                 '  #5 M0 Free, theirs [build]', 'Decisions waiting for me (0)',
                 'Not shown: 0 blocked, 0 taken by others, 0 threads (--all)']
        self.assertEqual(PLAN.queue(ISSUES, 'me'), today)
        self.assertEqual(PLAN.queue(ISSUES, 'me', held=HELD), today)
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'claims'
            self.assertEqual(self.run_main([], folder), (0, '\n'.join(today) + '\n'))
            self.assertFalse(folder.exists())
            C.claim(1, 'claude', folder=folder)
            self.assertEqual(self.run_main([], folder), (0, '\n'.join(today) + '\n'))   # claims are not read

    def test_claim_release_and_agent_from_the_command_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'claims'
            code, out = self.run_main(['claim', '4', '--agent', 'codex'], folder)
            self.assertEqual((code, out.split(' (')[0]), (0, '#4 claimed by codex'))
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(self.run_main(['claim', '4', '--agent', 'claude'], folder)[0], 1)
            self.assertIn('refused: #4 is claimed by codex', err.getvalue())
            with mock.patch.object(C, 'pr_states', lambda branch: None), \
                    mock.patch.object(C, 'last_commit', lambda branch: None):
                code, out = self.run_main(['--agent', 'codex'], folder)
            self.assertIn('Yours, in progress (claimed by codex) (1)\n  #4 M0 Free [build] (', out)
            self.assertEqual(self.run_main(['release', '4'], folder), (0, '#4 released (codex)\n'))
            self.assertEqual(self.run_main(['release', '4'], folder), (0, '#4 was not claimed\n'))


HOLD = ('import sys, time; sys.path.insert(0, %r); from xy_forever import device_lock; '
        'device_lock.hold(float(sys.argv[3]), sys.argv[1], sys.argv[2]); print("held", flush=True); '
        'time.sleep(float(sys.argv[4]))' % str(ROOT))


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
        got = self.second(0.5)
        self.assertIn('waiting up to 0.5 s', got.stderr)
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
        import xyf_host
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
            own = CHECK.fresh_build_folder('a' * 40, now)
            self.assertEqual(own, builds / 'check-aaaaaaaaaaaa')
            self.assertEqual(sorted(p.name for p in builds.iterdir()),
                             [images[1]] + images[3:] + ['check-bbbbbbbbbbbb'])


if __name__ == '__main__':
    unittest.main()
