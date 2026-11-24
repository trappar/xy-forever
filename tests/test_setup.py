"""scripts/setup.py with synthetic stand-ins for the stock files (never the real bytes): the pin check, the 1.2.5
refusal, unknown files, a copied /upgrade/ tree as input, and where the private folder comes from
(XYF_PRIVATE > xyf.local.json > ../private), the Python check (3.14+), and `bootstrap` in a temporary workspace
with the build image's pull and the tool steps (mise, uv, pnpm) stubbed (no network or Docker)."""

import contextlib
import hashlib
import importlib.util
import io
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pytest

from xy_forever import dev_tools, modlink, outer, paths, usb_proto

ROOT = Path(__file__).resolve().parents[1]

spec = importlib.util.spec_from_file_location('xyf_setup', ROOT / 'scripts/setup.py')
S = importlib.util.module_from_spec(spec)
spec.loader.exec_module(S)

FILES = {
    ('1.2.1', 'os.ldr'): b'os 1.2.1',
    ('1.2.1', 'bootloader.ldr'): b'boot 1.2.1',
    ('1.2.5', 'os.ldr'): b'os 1.2.5',
    ('1.2.5', 'bootloader.ldr'): b'boot 1.2.5',
}
PINS = {}
for (version, name), data in FILES.items():
    PINS.setdefault(version, {})[name] = hashlib.sha256(data).hexdigest()


class ImportStock(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.work = Path(tmp.name)
        self.dest = self.work / 'private/firmware/stock-1.2.1'

    def pair(self, folder, os_version='1.2.1', boot_version='1.2.1', os_bytes=None):
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'os.ldr').write_bytes(os_bytes if os_bytes is not None else FILES[os_version, 'os.ldr'])
        (folder / 'bootloader.ldr').write_bytes(FILES[boot_version, 'bootloader.ldr'])
        return folder

    def imported(self, folder):
        return S.import_stock(folder, self.dest, PINS)

    def test_pinned_121_pair_is_copied(self):
        self.imported(self.pair(self.work / 'copy'))
        for name in S.PAIR:
            self.assertEqual((self.dest / name).read_bytes(), FILES['1.2.1', name])
        self.imported(self.work / 'copy')  # again: unchanged, no error
        self.assertEqual(sorted(p.name for p in self.dest.iterdir()), sorted(S.PAIR))

    def test_upgrade_tree_as_copied_or_its_parent(self):
        self.pair(self.work / 'device/upgrade/firmware/blackfin/bin')
        (self.work / 'device/upgrade/firmware/versions.txt').write_text('blackfin 1.2.1+0\n')
        for given in (self.work / 'device/upgrade', self.work / 'device'):
            with self.subTest(given=given.name):
                self.imported(given)
                self.assertEqual((self.dest / 'os.ldr').read_bytes(), FILES['1.2.1', 'os.ldr'])

    def test_125_device_is_refused_as_unsupported(self):
        for os_version, boot_version in (('1.2.5', '1.2.5'), ('1.2.1', '1.2.5'), ('1.2.5', '1.2.1')):
            with self.subTest(os=os_version, boot=boot_version):
                folder = self.pair(self.work / (os_version + boot_version), os_version, boot_version)
                with self.assertRaises(S.SetupError) as caught:
                    self.imported(folder)
                message = str(caught.exception)
                if boot_version == '1.2.5':
                    self.assertRegex(message, r'this OP-XY is on 1\.2\.5.*cannot be installed')
                    self.assertIn('cannot be downgraded', message)
                    self.assertIn('needs a device still on 1.2.1', message)
                else:
                    self.assertIn('needs the stock 1.2.1 pair', message)
                self.assertNotRegex(message, r'(?i)install the official|copy .*again|teenage\.engineering/guides')
                self.assertFalse(self.dest.exists())

    def test_unknown_or_swapped_file_is_refused(self):
        for label, folder in (
            ('changed', self.pair(self.work / 'a', os_bytes=b'os 1.2.1 patched')),
            ('swapped', self.pair(self.work / 'b', os_bytes=FILES['1.2.1', 'bootloader.ldr'])),
        ):
            with self.subTest(label), self.assertRaisesRegex(S.SetupError, 'os.ldr: not a known stock file'):
                self.imported(folder)
        self.assertFalse(self.dest.exists())

    def test_missing_pair_says_where_to_look(self):
        (self.work / 'half').mkdir()
        (self.work / 'half/os.ldr').write_bytes(FILES['1.2.1', 'os.ldr'])
        with self.assertRaisesRegex(S.SetupError, r'no os.ldr \+ bootloader.ldr .*/upgrade/firmware/blackfin/bin/'):
            self.imported(self.work / 'half')


CATALOG = b'{"synthetic": "stock catalog"}'


class StockCatalog(unittest.TestCase):
    """The device's /content/op1_factory.json (a synthetic stand-in, its pin patched): kept by import-stock only
    if it matches; fixtures downloads (git_show stubbed) only when no valid copy is present."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.work = Path(tmp.name)
        self.dest = self.work / 'private/firmware/stock-1.2.1'
        patcher = mock.patch.dict(S.PINS['open_xy'], {'catalog_sha256': hashlib.sha256(CATALOG).hexdigest()})
        patcher.start()
        self.addCleanup(patcher.stop)

    def imported(self, folder):
        for name in S.PAIR:
            (folder / name).write_bytes(FILES['1.2.1', name])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            S.import_stock(folder, self.dest, PINS)
        return out.getvalue()

    def test_matching_copy_beside_the_pair_or_in_a_content_folder_is_kept(self):
        for label, where in (('beside', ''), ('content', 'content')):
            with self.subTest(label):
                folder = self.work / label
                (folder / where).mkdir(parents=True)
                (folder / where / 'op1_factory.json').write_bytes(CATALOG)
                out = self.imported(folder)
                self.assertEqual((self.dest / 'op1_factory.json').read_bytes(), CATALOG)
                self.assertIn('from your device copy', out)
                (self.dest / 'op1_factory.json').unlink()

    def test_a_modified_copy_is_reported_and_ignored(self):
        folder = self.work / 'modded'
        (folder / 'content').mkdir(parents=True)
        (folder / 'content/op1_factory.json').write_bytes(CATALOG + b' modded')
        out = self.imported(folder)
        self.assertEqual(len(out.strip().splitlines()), 1)
        self.assertRegex(out, r'ignored, not the stock 1\.2\.1 file .*Open-XY mods rewrite it')
        self.assertEqual(sorted(p.name for p in self.dest.iterdir()), sorted(S.PAIR))

    def fixture_catalog(self, served):
        shown = []
        out = io.StringIO()
        with (
            mock.patch.object(S.paths, 'STOCK_121', self.dest),
            mock.patch.object(S.paths, 'CATALOG_121', self.dest / 'op1_factory.json'),
            mock.patch.object(S, 'git_show', lambda path: shown.append(path) or served),
            contextlib.redirect_stdout(out),
        ):
            S.fixture_catalog()
        return shown, out.getvalue()

    def test_fixtures_download_only_without_a_valid_copy(self):
        self.dest.mkdir(parents=True)
        for label, present in (('none', None), ('modified', CATALOG + b' modded')):
            with self.subTest(label):
                if present:
                    (self.dest / 'op1_factory.json').write_bytes(present)
                shown, out = self.fixture_catalog(CATALOG)
                self.assertEqual(shown, [S.PINS['open_xy']['catalog_path']])
                self.assertIn('from open-xy %s' % S.PINS['open_xy']['sdk_commit'][:7], out)
                self.assertEqual((self.dest / 'op1_factory.json').read_bytes(), CATALOG)
                (self.dest / 'op1_factory.json').unlink()
        (self.dest / 'op1_factory.json').write_bytes(CATALOG)
        shown, out = self.fixture_catalog(b'never used')
        self.assertEqual(shown, [])
        self.assertIn('no download', out)
        with self.assertRaisesRegex(S.SetupError, 'does not match the pinned stock catalog'):
            (self.dest / 'op1_factory.json').unlink()
            self.fixture_catalog(CATALOG + b' upstream changed')


class Pins(unittest.TestCase):
    def test_stock_pins_agree_with_the_code(self):
        self.assertEqual(
            S.PINS['stock']['1.2.1'], {'os.ldr': usb_proto.STOCK_OS_LDR_SHA256, 'bootloader.ldr': outer.BOOT_HASH}
        )
        self.assertEqual(S.identify('os.ldr', usb_proto.STOCK_OS_LDR_SHA256), '1.2.1')
        self.assertEqual(S.identify('bootloader.ldr', usb_proto.STOCK_OS_LDR_SHA256), None)
        self.assertEqual(set(S.PINS['stock']), {'1.2.1', '1.2.5'})

    def test_open_xy_pin_is_the_mod_sdk(self):
        self.assertEqual(S.PINS['open_xy']['sdk_commit'], modlink.SDK_COMMIT)
        self.assertEqual(paths.PUBLISHED_MODS.parent.name, 'opxy-1.2.1')


class PythonVersion(unittest.TestCase):
    def test_314_or_newer_runs(self):
        for v in ((3, 14, 0), (3, 15, 2), (4, 0, 0)):
            self.assertIsNone(S.python_problem(v))
            S.require_python(v)

    def test_older_than_314_is_refused_before_doing_anything(self):
        self.assertRegex(S.python_problem((3, 9, 6)), r'3\.9\.6 is too old: XY Forever needs 3\.14\+.*mise run setup')
        old = (3, 8, 10, 'final', 0)
        with mock.patch.object(S.sys, 'version_info', old), mock.patch.object(S, 'make_layout') as layout:
            for cmd in (S.cmd_init, S.cmd_fixtures):
                with self.assertRaisesRegex(S.SetupError, 'too old'):
                    cmd(S.argparse.Namespace(private=None))
        layout.assert_not_called()


class PrivateRoot(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.work = Path(tmp.name).resolve()
        self.config = self.work / 'xyf.local.json'

    def test_env_then_config_then_default(self):
        S.write_config(self.work / 'mine', self.config)
        self.assertEqual(json.loads(self.config.read_text()), {'private': str(self.work / 'mine')})
        env = {'XYF_PRIVATE': str(self.work / 'env')}
        self.assertEqual(paths.private_root(env, self.config), self.work / 'env')
        self.assertEqual(paths.private_root({}, self.config), self.work / 'mine')
        self.assertEqual(paths.private_root({'XYF_PRIVATE': ''}, self.config), self.work / 'mine')
        self.assertEqual(paths.private_root({}, self.work / 'absent.json'), (paths.MAIN.parent / 'private').resolve())

    def test_config_refuses_a_checkout(self):
        with self.assertRaisesRegex(S.SetupError, 'outside every git checkout'):
            S.write_config(ROOT / 'private', self.config)
        self.assertFalse(self.config.exists())

    def test_init_adds_an_empty_leak_list_and_keeps_other_keys(self):
        S.ensure_leak_patterns(self.config)
        self.assertEqual(json.loads(self.config.read_text()), {'leak_patterns': []})
        self.assertEqual(paths.private_root({}, self.config), (paths.MAIN.parent / 'private').resolve())
        self.config.write_text(json.dumps({'private': 'p', 'other': 1}))
        S.ensure_leak_patterns(self.config)
        self.assertEqual(json.loads(self.config.read_text()), {'private': 'p', 'other': 1, 'leak_patterns': []})
        self.config.write_text(json.dumps({'leak_patterns': ['(?i)jane']}))
        S.ensure_leak_patterns(self.config)
        self.assertEqual(json.loads(self.config.read_text()), {'leak_patterns': ['(?i)jane']})


class Bootstrap(unittest.TestCase):
    """bootstrap in a temporary workspace: the build image (Docker) and mise, uv and pnpm are stubbed (dev_tools:
    what is installed is self.done; the image is pulled when self.image is None)."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.ws = Path(tmp.name).resolve() / 'ws'
        self.repo = self.ws / 'xy-forever'
        (self.repo / 'docs').mkdir(parents=True)
        (self.repo / 'docs/STATUS.template.md').write_text('# status\n')
        self.config = self.repo / 'xyf.local.json'
        self.calls, self.stock, self.which, self.done = [], False, {'mise'}, set()
        self.image = 'not pulled yet (mise run setup pulls it)'
        for name, value in (
            ('run', self.fake_run),
            ('image_problem', lambda: self.image),
            ('pull_image', self.fake_pull),
            ('stock_present', lambda: self.stock),
            ('cmd_doctor', lambda args: 1),
            ('cmd_fixtures', lambda args: self.calls.append('fixtures')),
            ('fixtures_missing', lambda: ['verified build']),
        ):
            patcher = mock.patch.object(S, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        stale = lambda name, why: lambda root=None: None if name in self.done else why
        for name, value in (
            ('mise', lambda: '/bin/mise' if 'mise' in self.which else None),
            (
                'tools_missing',
                lambda root=None: (
                    'mise not found: install it'
                    if 'mise' not in self.which
                    else stale('mise', 'missing python: run `mise install`')()
                ),
            ),
            ('python_deps_stale', stale('uv', 'run `uv sync`')),
            ('js_deps_stale', stale('pnpm', 'run `pnpm install`')),
            ('hook_missing', stale('lefthook', 'run `lefthook install`')),
            ('tool', lambda name: ['/bin/' + name]),
        ):
            patcher = mock.patch.object(S.dev_tools, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(S.shutil, 'which', self.fake_which)
        patcher.start()
        self.addCleanup(patcher.stop)

    def fake_run(self, cmd, cwd=None, env=None):
        self.calls.append(' '.join(map(str, cmd[:2])))
        self.done.add(Path(cmd[0]).name)

    def fake_pull(self):
        self.calls.append('pull')
        self.image = None

    def fake_which(self, name):
        return '/bin/' + name if name in self.which else None

    def bootstrap(self, dry_run=False, workspace=None, environ=None):
        out = io.StringIO()
        b = S.Bootstrap(workspace or self.ws, self.repo, self.config, dry_run, environ or {})
        with contextlib.redirect_stdout(out):
            b.run()
        return b, out.getvalue()

    def tree(self):
        return sorted(
            (str(p.relative_to(self.ws)), p.is_symlink(), p.read_bytes() if p.is_file() else None)
            for p in self.ws.rglob('*')
        )

    def test_fresh_workspace_then_a_second_run_changes_nothing(self):
        b, out = self.bootstrap()
        for name in S.LAYOUT:
            self.assertTrue((self.ws / 'private' / getattr(paths, name).relative_to(paths.PRIVATE_ROOT)).is_dir())
        self.assertTrue((self.ws / 'private/incoming').is_dir())
        self.assertEqual((self.ws / 'private/xy-forever/status/README.md').read_text(), '# status\n')
        self.assertEqual(json.loads(self.config.read_text()), {'leak_patterns': []})  # ../private: nothing recorded
        self.assertEqual(
            self.calls,
            [
                '/bin/mise install',
                '/bin/uv sync',
                '/bin/pnpm install',
                '/bin/lefthook install',
                'pull',
            ],
        )
        self.assertEqual((self.ws / 'CLAUDE.md').read_text(), '@AGENTS.md\n')
        guide = (self.ws / 'AGENTS.md').read_text()
        self.assertIn('Read `xy-forever/AGENTS.md` first and follow it', guide)
        self.assertIn('"Continue development" works from here', guide)
        self.assertTrue(10 <= len(guide.splitlines()) <= 15)
        self.assertRegex(
            out,
            r'Next:\n  1\. Add your name, emails and host name as regexes to "leak_patterns" .*\n'
            r'  2\. Put your OP-XY .* in MTP mode: hold COM .*\n  3\. uv run scripts/setup\.py '
            r'import-stock --from-device +\(reads them into %s.*/upgrade/firmware/blackfin/bin/os\.ldr'
            % (self.ws / 'private/incoming'),
        )
        self.assertIn('import-stock %s' % (self.ws / 'private/incoming'), out)
        self.assertIn('5. Open your agent in %s (or %s) and say "Continue development"' % (self.ws, self.repo), out)
        before, self.calls = self.tree(), []
        b, out = self.bootstrap()
        self.assertEqual((b.actions, self.calls, self.tree()), ([], [], before))
        self.assertIn('nothing to do: the workspace is already set up; nothing changed', out)

    def test_private_given_is_recorded_once_and_used(self):
        chosen = self.ws / 'elsewhere'
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            S.Bootstrap(self.ws, self.repo, self.config, False, {}, private=chosen).run()
        self.assertEqual(json.loads(self.config.read_text())['private'], str(chosen))
        self.assertTrue((chosen / 'incoming').is_dir())
        with contextlib.redirect_stdout(io.StringIO()):
            again = S.Bootstrap(self.ws, self.repo, self.config, False, {}, private=chosen)
            again.run()
        self.assertEqual(again.actions, [])

    def test_status_notes_in_the_checkout_move_to_the_private_folder_once(self):
        (self.repo / 'docs/status/approvals').mkdir(parents=True)
        (self.repo / 'docs/status/README.md').write_text('# mine\n')
        (self.repo / 'docs/status/approvals/a.md').write_text('yes\n')
        b, out = self.bootstrap()
        status = self.ws / 'private/xy-forever/status'
        self.assertEqual(
            ((status / 'README.md').read_text(), (status / 'approvals/a.md').read_text()), ('# mine\n', 'yes\n')
        )
        self.assertFalse((self.repo / 'docs/status').exists())
        self.assertIn('move %s to %s' % (self.repo / 'docs/status', status), out)
        b, out = self.bootstrap()
        self.assertNotIn('move', ' '.join(b.actions))
        self.assertEqual((status / 'README.md').read_text(), '# mine\n')

    def test_status_notes_already_moved_leave_a_second_copy_alone(self):
        (self.ws / 'private/xy-forever/status').mkdir(parents=True)
        (self.ws / 'private/xy-forever/status/README.md').write_text('# moved\n')
        (self.repo / 'docs/status').mkdir()
        (self.repo / 'docs/status/README.md').write_text('# old\n')
        b, out = self.bootstrap()
        self.assertEqual((self.ws / 'private/xy-forever/status/README.md').read_text(), '# moved\n')
        self.assertEqual((self.repo / 'docs/status/README.md').read_text(), '# old\n')
        self.assertIn('merge it by hand', out)

    def test_dry_run_prints_every_action_and_changes_nothing(self):
        before = self.tree()
        b, out = self.bootstrap(dry_run=True)
        self.assertEqual((self.calls, self.tree()), ([], before))
        for what in (
            'create 12 folders',
            'copy docs/STATUS.template.md to %s' % (self.ws / 'private/xy-forever/status/README.md'),
            '"leak_patterns"',
            'write %s' % (self.ws / 'AGENTS.md'),
            'write %s' % (self.ws / 'CLAUDE.md'),
            'pull the build image',
            'mise install',
            'uv sync --locked',
            'pnpm install --frozen-lockfile',
            'lefthook install',
        ):
            self.assertRegex(out, r'  would .*' + re.escape(what))
        self.assertIn('dry run: the 10 actions above would run; nothing changed', out)

    def test_an_image_already_pulled_or_no_docker_pulls_nothing(self):
        self.image = None
        b, out = self.bootstrap()
        self.assertIn('build image: ok', out)
        self.assertEqual(
            self.calls, ['/bin/mise install', '/bin/uv sync', '/bin/pnpm install', '/bin/lefthook install']
        )
        self.calls, self.image = [], 'Docker is needed for firmware builds and is not installed'
        b, out = self.bootstrap()
        self.assertIn('docs/operating/dev-tools.md says what each OS needs', out)
        self.assertEqual(self.calls, [])

    def test_no_mise_skips_the_tools_with_a_note(self):
        self.which = set()
        b, out = self.bootstrap()
        self.assertEqual(self.calls, ['pull'])
        self.assertIn('tools: skipped: mise not found', out)

    def test_another_workspace_is_recorded_unless_the_private_folder_is_set(self):
        other = self.ws.parent / 'other'
        b, out = self.bootstrap(workspace=other)
        self.assertEqual(json.loads(self.config.read_text()), {'private': str(other / 'private'), 'leak_patterns': []})
        self.assertTrue((other / 'private/firmware/stock-1.2.1').is_dir())
        self.assertIn('workspace AGENTS.md: skipped', out)
        self.assertFalse((other / 'AGENTS.md').exists())
        self.config.unlink()
        b, out = self.bootstrap(workspace=other, environ={'XYF_PRIVATE': str(self.ws / 'env')})
        self.assertEqual(json.loads(self.config.read_text()), {'leak_patterns': []})
        self.assertIn('private folder: %s (XYF_PRIVATE)' % (self.ws / 'env'), out)
        self.assertTrue((self.ws / 'env/xy-forever/builds').is_dir())

    def test_imported_stock_pair_runs_fixtures_and_shortens_next(self):
        self.stock = True
        (self.ws / 'private/firmware/stock-1.2.1').mkdir(parents=True)
        (self.ws / 'private/firmware/stock-1.2.1/os.ldr').write_bytes(b'os')
        b, out = self.bootstrap()
        self.assertEqual(self.calls[-1], 'fixtures')
        self.assertFalse((self.ws / 'private/incoming').exists())
        self.assertRegex(out, r'Next:\n  1\. Add your name.*\n  2\. Open your agent in ')

    def test_the_workspace_guide_names_a_private_folder_elsewhere_in_full(self):
        elsewhere = self.ws.parent / 'elsewhere'
        self.bootstrap(environ={'XYF_PRIVATE': str(elsewhere)})
        self.assertIn('| `%s/` | stock firmware' % elsewhere, (self.ws / 'AGENTS.md').read_text())

    def test_existing_workspace_guides_are_never_overwritten(self):
        self.ws.joinpath('AGENTS.md').write_text('mine\n')
        b, out = self.bootstrap()
        self.assertEqual((self.ws / 'AGENTS.md').read_text(), 'mine\n')
        self.assertIn('workspace AGENTS.md: present (left as it is; bootstrap never overwrites it)', out)
        self.assertEqual((self.ws / 'CLAUDE.md').read_text(), '@AGENTS.md\n')


@pytest.mark.gate
class BareInterpreter(unittest.TestCase):
    """`mise run setup` starts on mise's Python, without the project environment (#229): doctor and a dry run work
    there, on the standard library only (-S: no site-packages), with a private folder of their own."""

    def setup(self, *args, path=None):
        with tempfile.TemporaryDirectory() as tmp:
            env = {k: v for k, v in S.os.environ.items() if not k.startswith(('VIRTUAL_ENV', 'PYTHON', 'UV_'))}
            env['XYF_PRIVATE'] = str(Path(tmp) / 'private')
            if path is not None:
                env['PATH'] = path
            bare = getattr(sys, '_base_executable', sys.executable)
            cmd = [bare, '-S', str(ROOT / 'scripts/setup.py'), *args]
            run = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
            self.assertFalse(list(Path(tmp).iterdir()), 'a dry run or doctor changed nothing')
        self.assertNotIn('Traceback', run.stderr)
        return run

    def test_doctor_reports_the_build_as_unknown(self):
        run = self.setup('doctor')
        self.assertRegex(run.stdout, r'-- +verified build +%s' % re.escape(S.NO_ENV))

    def test_dry_run_without_uv_stays_on_the_standard_library(self):
        run = self.setup('bootstrap', '--dry-run', path='/usr/bin:/bin')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertNotIn('continuing under uv run', run.stdout)
        self.assertIn('dry run: the ', run.stdout)

    def test_dry_run_continues_under_uv_once_the_dependencies_are_synced(self):
        run = self.setup('bootstrap', '--dry-run')
        self.assertEqual(run.returncode, 0, run.stderr)
        synced = dev_tools.python_deps_stale() is None
        self.assertEqual('continuing under uv run' in run.stdout, synced)
        self.assertIn('dry run: the ', run.stdout)


@pytest.mark.gate
class DevToolRows(unittest.TestCase):
    """doctor's tool rows (xy_forever/dev_tools.py): mise, its pins, the Python and the JS dependencies."""

    def test_tool_and_dependency_rows_must_pass(self):
        fine = [
            (label, True, 'ok')
            for label in (
                'python',
                'private folder',
                'build image',
                'stock os.ldr',
                'stock bootloader.ldr',
                'mise',
                'tools',
                'python deps',
                'js deps',
                'git hook',
            )
        ]
        for label in ('mise', 'tools', 'python deps', 'js deps', 'git hook', 'build image'):
            rows = [(l, l != label, d) for l, _, d in fine] + [('verified build', False, 'none')]
            with mock.patch.object(S, 'doctor', return_value=(rows, [])), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(S.cmd_doctor(None), 1, label)
        with (
            mock.patch.object(S, 'doctor', return_value=(fine + [('verified build', False, 'none')], [])),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(S.cmd_doctor(None), 0)  # a missing build or fixture only skips tests
        no_stock = [(l, not l.startswith('stock'), d) for l, _, d in fine]
        with mock.patch.object(S, 'doctor', return_value=(no_stock, [])), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(S.cmd_doctor(None), 0)  # no OP-XY yet: setup without a device is done

    def test_tools_missing(self):
        with mock.patch.object(dev_tools, 'mise', lambda: None):
            self.assertRegex(dev_tools.tools_missing(), '^mise not found: install it')
        for out, rc, err, want in (
            ('{}', 0, '', None),
            ('{"node": []}', 0, '', 'missing node: run `mise install`'),
            ('', 1, 'mise.toml is not trusted\n', 'mise.toml is not trusted (`mise trust` here'),
        ):
            done = mock.Mock(returncode=rc, stdout=out, stderr=err)
            with (
                mock.patch.object(dev_tools, 'mise', lambda: '/bin/mise'),
                mock.patch.object(dev_tools, 'quiet', return_value=done),
            ):
                problem = dev_tools.tools_missing()
            self.assertEqual(problem, want) if want is None else self.assertIn(want, problem)

    def test_js_deps_follow_the_lockfile(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'pnpm-lock.yaml').write_text('lock')
            self.assertEqual(dev_tools.js_deps_stale(root), 'run `pnpm install`')
            for rel in ('node_modules', 'web/node_modules', 'clients/ts/node_modules'):
                (root / rel).mkdir(parents=True)
            (root / 'node_modules/.modules.yaml').write_text('')
            self.assertIsNone(dev_tools.js_deps_stale(root))
            later = (root / 'node_modules/.modules.yaml').stat().st_mtime + 10
            dev_tools.os.utime(root / 'pnpm-lock.yaml', (later, later))
            self.assertIn('pnpm-lock.yaml changed', dev_tools.js_deps_stale(root))

    def test_hook_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertIsNone(dev_tools.hook_missing(root))  # no git checkout: nothing to install
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            self.assertEqual(dev_tools.hook_missing(root), 'run `lefthook install`')
            (root / '.git/hooks/pre-commit').write_text('#!/bin/sh\nlefthook run pre-commit "$@"\n')
            self.assertIsNone(dev_tools.hook_missing(root))

    def test_global_mise_config_is_kept_out(self):
        self.assertEqual(dev_tools.env({})['MISE_IGNORED_CONFIG_PATHS'], str(Path.home() / '.config/mise'))

    def test_doctor_reports_them(self):
        labels = [r[0] for r in S.doctor()[0]]
        self.assertEqual(
            [x for x in labels if x in ('mise', 'tools', 'python deps', 'js deps')],
            ['mise', 'tools', 'python deps', 'js deps'],
        )


if __name__ == '__main__':
    unittest.main()
