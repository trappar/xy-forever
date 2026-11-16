"""scripts/setup.py with synthetic stand-ins for the stock files (never the real bytes): the pin check, the 1.2.5
refusal, unknown files, a copied /upgrade/ tree as input, and where the private folder comes from
(XYF_PRIVATE > xyf.local.json > ../private), the Python check (3.9+; 3.11+ for open-xy's sdk.py), and `bootstrap`
in a temporary workspace with git, open-xy's toolchain fetch and npm stubbed (no network)."""
import contextlib
import hashlib
import io
import re
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from xy_forever import modlink, outer, paths, usb_proto  # noqa: E402

spec = importlib.util.spec_from_file_location('xyf_setup', ROOT / 'scripts/setup.py')
S = importlib.util.module_from_spec(spec)
spec.loader.exec_module(S)

FILES = {('1.2.1', 'os.ldr'): b'os 1.2.1', ('1.2.1', 'bootloader.ldr'): b'boot 1.2.1',
         ('1.2.5', 'os.ldr'): b'os 1.2.5', ('1.2.5', 'bootloader.ldr'): b'boot 1.2.5'}
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
        self.imported(self.work / 'copy')                        # again: unchanged, no error
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
        for label, folder in (('changed', self.pair(self.work / 'a', os_bytes=b'os 1.2.1 patched')),
                              ('swapped', self.pair(self.work / 'b', os_bytes=FILES['1.2.1', 'bootloader.ldr']))):
            with self.subTest(label), self.assertRaisesRegex(S.SetupError, 'os.ldr: not a known stock file'):
                self.imported(folder)
        self.assertFalse(self.dest.exists())

    def test_missing_pair_says_where_to_look(self):
        (self.work / 'half').mkdir()
        (self.work / 'half/os.ldr').write_bytes(FILES['1.2.1', 'os.ldr'])
        with self.assertRaisesRegex(S.SetupError, r'no os.ldr \+ bootloader.ldr .*/upgrade/firmware/blackfin/bin/'):
            self.imported(self.work / 'half')


class Pins(unittest.TestCase):
    def test_stock_pins_agree_with_the_code(self):
        self.assertEqual(S.PINS['stock']['1.2.1'], {'os.ldr': usb_proto.STOCK_OS_LDR_SHA256,
                                                    'bootloader.ldr': outer.BOOT_HASH})
        self.assertEqual(S.identify('os.ldr', usb_proto.STOCK_OS_LDR_SHA256), '1.2.1')
        self.assertEqual(S.identify('bootloader.ldr', usb_proto.STOCK_OS_LDR_SHA256), None)
        self.assertEqual(set(S.PINS['stock']), {'1.2.1', '1.2.5'})

    def test_open_xy_pin_is_the_mod_sdk(self):
        self.assertEqual(S.PINS['open_xy']['sdk_commit'], modlink.SDK_COMMIT)
        self.assertEqual(paths.PUBLISHED_MODS.parent.name, 'opxy-1.2.1')


class PythonVersion(unittest.TestCase):
    def test_39_or_newer_runs_and_311_needs_no_note(self):
        for v in ((3, 9, 6), (3, 10, 14), (3, 11, 0), (3, 12, 4), (4, 0, 0)):
            self.assertIsNone(S.python_problem(v))
            S.require_python(v)
        for v in ((3, 11, 0), (4, 0, 0)):
            self.assertIsNone(S.python_warning(v))

    def test_39_and_310_warn_about_the_open_xy_sdk_only(self):
        for v in ((3, 9, 6), (3, 10, 14)):
            self.assertRegex(S.python_warning(v), r"%d\.%d\.%d: fine for XY Forever; 3\.11\+ is needed for "
                             r"open-xy's sdk\.py toolchain fetch" % v)

    def test_older_than_39_is_refused_before_doing_anything(self):
        self.assertRegex(S.python_problem((3, 8, 10)), r'3\.8\.10 is too old: XY Forever needs 3\.9\+.*python3\.12')
        self.assertIsNone(S.python_warning((3, 8, 10)))
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
        self.assertEqual(paths.private_root({}, self.work / 'absent.json'), (paths.REPO.parent / 'private').resolve())

    def test_config_refuses_a_checkout(self):
        with self.assertRaisesRegex(S.SetupError, 'outside every git checkout'):
            S.write_config(ROOT / 'private', self.config)
        self.assertFalse(self.config.exists())

    def test_init_adds_an_empty_leak_list_and_keeps_other_keys(self):
        S.ensure_leak_patterns(self.config)
        self.assertEqual(json.loads(self.config.read_text()), {'leak_patterns': []})
        self.assertEqual(paths.private_root({}, self.config), (paths.REPO.parent / 'private').resolve())
        self.config.write_text(json.dumps({'private': 'p', 'other': 1}))
        S.ensure_leak_patterns(self.config)
        self.assertEqual(json.loads(self.config.read_text()), {'private': 'p', 'other': 1, 'leak_patterns': []})
        self.config.write_text(json.dumps({'leak_patterns': ['(?i)jane']}))
        S.ensure_leak_patterns(self.config)
        self.assertEqual(json.loads(self.config.read_text()), {'leak_patterns': ['(?i)jane']})

    def test_local_config_is_gitignored(self):
        self.assertIn(paths.LOCAL_CONFIG.name, (ROOT / '.gitignore').read_text().split())


class Bootstrap(unittest.TestCase):
    """bootstrap in a temporary workspace: git clone, `sdk.py toolchain fetch` and npm ci are stubbed."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.ws = Path(tmp.name).resolve() / 'ws'
        self.repo = self.ws / 'xy-forever'
        (self.repo / 'docs').mkdir(parents=True)
        (self.repo / 'docs/STATUS.template.md').write_text('# status\n')
        for d in S.NPM_DIRS:
            (self.repo / d).mkdir(parents=True)
        self.config = self.repo / 'xyf.local.json'
        self.calls, self.stock, self.which = [], False, {'node', 'npm', 'python3.14'}
        self.version = (3, 12)
        for name, value in (('run', self.fake_run), ('toolchain_problem', self.fake_toolchain),
                            ('stock_present', lambda: self.stock), ('cmd_doctor', lambda args: 1),
                            ('cmd_fixtures', lambda args: self.calls.append('fixtures')),
                            ('fixtures_missing', lambda: ['verified build']), ('host_name', lambda: 'darwin-arm64'),
                            ('python_version', lambda exe: self.version)):
            patcher = mock.patch.object(S, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for target, name, value in ((S.shutil, 'which', self.fake_which), (S.subprocess, 'run', self.fake_subprocess)):
            patcher = mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def fake_run(self, cmd, cwd=None):
        self.calls.append(' '.join(map(str, cmd[:2])))
        if cmd[0] == 'git':
            Path(cmd[-1]).mkdir(parents=True)
            (Path(cmd[-1]) / 'sdk.py').write_text('')
        else:
            (Path(cwd) / 'node_modules').mkdir()

    def fake_subprocess(self, cmd, cwd=None, **kw):             # only `sdk.py toolchain fetch` gets here
        self.assertEqual(cmd[1:], ['sdk.py', 'toolchain', 'fetch'])
        self.calls.append('fetch ' + cmd[0])
        (Path(cwd) / 'local/toolchain/bin').mkdir(parents=True)
        return S.subprocess.CompletedProcess(cmd, 0)

    def fake_which(self, name):
        return '/bin/' + name if name in self.which else None

    def fake_toolchain(self, directory=None):
        return None if directory and (Path(directory) / 'bin').is_dir() else 'missing'

    def bootstrap(self, dry_run=False, workspace=None, environ=None):
        out = io.StringIO()
        b = S.Bootstrap(workspace or self.ws, self.repo, self.config, dry_run, environ or {})
        with contextlib.redirect_stdout(out):
            b.run()
        return b, out.getvalue()

    def tree(self):
        return sorted((str(p.relative_to(self.ws)), p.is_symlink(), p.read_bytes() if p.is_file() else None)
                      for p in self.ws.rglob('*'))

    def test_fresh_workspace_then_a_second_run_changes_nothing(self):
        b, out = self.bootstrap()
        for name in S.LAYOUT:
            self.assertTrue((self.ws / 'private' / getattr(paths, name).relative_to(paths.PRIVATE_ROOT)).is_dir())
        self.assertTrue((self.ws / 'private/incoming').is_dir())
        self.assertEqual((self.repo / 'docs/STATUS.md').read_text(), '# status\n')
        self.assertEqual(json.loads(self.config.read_text()), {'leak_patterns': []})   # ../private: nothing recorded
        link = self.ws / 'tools/blackfin'
        self.assertEqual(Path(S.os.readlink(link)), self.ws / 'tools/open-xy/local/toolchain')
        self.assertEqual(self.calls, ['git clone', 'fetch ' + sys.executable if sys.version_info >= (3, 11) else
                                      'fetch /bin/python3.14', '/bin/npm ci', '/bin/npm ci'])
        self.assertRegex(out, r'Next:\n  1\. Copy os\.ldr and bootloader\.ldr .* into %s.*/upgrade/firmware/blackfin/bin/'
                         % (self.ws / 'private/incoming'))
        self.assertIn('import-stock %s' % (self.ws / 'private/incoming'), out)
        self.assertRegex(out, r'4\. Open Claude Code in %s and say "Continue development"' % self.repo)
        before, self.calls = self.tree(), []
        b, out = self.bootstrap()
        self.assertEqual((b.actions, self.calls, self.tree()), ([], [], before))
        self.assertIn('nothing to do: the workspace is already set up; nothing changed', out)

    def test_dry_run_prints_every_action_and_changes_nothing(self):
        before = self.tree()
        b, out = self.bootstrap(dry_run=True)
        self.assertEqual((self.calls, self.tree()), ([], before))
        for what in ('create 12 folders', 'copy docs/STATUS.template.md', '"leak_patterns"', 'clone https://github.com/',
                     'sdk.py toolchain fetch', 'link %s' % (self.ws / 'tools/blackfin'), 'npm ci in clients/ts',
                     'npm ci in web'):
            self.assertRegex(out, r'  would .*' + re.escape(what))
        self.assertIn('dry run: the 8 actions above would run; nothing changed', out)

    def test_old_python_without_a_311_stops_with_how_to_rerun(self):
        self.which = {'node', 'npm', 'python3.13'}
        self.version = None                                            # a shim with no version installed
        with mock.patch.object(S.sys, 'version_info', (3, 9, 6, 'final', 0)):
            with self.assertRaises(S.SetupError) as caught:
                self.bootstrap()
            message = str(caught.exception)
            self.assertRegex(message, r'needs Python 3\.11\+; this is 3\.9\.6 .*brew install python@3\.12')
            self.assertIn('rerun `python3 scripts/setup.py bootstrap`', message)
            self.assertIn('python3.13 is there but does not run', message)
            self.assertTrue((self.repo / 'docs/STATUS.md').exists())   # what came before stays done
            self.assertEqual(self.calls, ['git clone'])
            self.version = (3, 12)
            self.assertEqual(S.sdk_python(), '/bin/python3.13')
        with mock.patch.object(S.sys, 'version_info', (3, 11, 2, 'final', 0)):
            self.assertEqual(S.sdk_python(), sys.executable)

    def test_toolchain_already_set_up_or_linux_is_not_fetched(self):
        with mock.patch.object(S, 'toolchain_problem', lambda directory=None: None):
            b, out = self.bootstrap()
        self.assertIn('toolchain: ok', out)
        self.assertEqual(self.calls, ['/bin/npm ci', '/bin/npm ci'])
        self.calls = []
        with mock.patch.object(S, 'host_name', lambda: 'linux-x86_64'):
            b, out = self.bootstrap()
        self.assertIn('build it with Docker (open-xy docs/build.md, "On Linux")', out)
        self.assertEqual((self.calls, (self.ws / 'tools').exists()), ([], False))

    def test_no_node_skips_npm_with_a_note(self):
        self.which = {'python3.14'}
        b, out = self.bootstrap()
        self.assertNotIn('/bin/npm ci', self.calls)
        self.assertIn('clients/ts: skipped: Node not found', out)

    def test_another_workspace_is_recorded_unless_the_private_folder_is_set(self):
        other = self.ws.parent / 'other'
        with mock.patch.object(S, 'host_name', lambda: 'linux-x86_64'):
            self.bootstrap(workspace=other)
            self.assertEqual(json.loads(self.config.read_text()), {'private': str(other / 'private'), 'leak_patterns': []})
            self.assertTrue((other / 'private/firmware/stock-1.2.1').is_dir())
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
        self.assertRegex(out, r'Next:\n  1\. Open Claude Code in ')

    def test_a_link_that_fails_its_pins_is_left_alone(self):
        link = self.ws / 'tools/blackfin'
        link.parent.mkdir(parents=True)
        S.os.symlink(str(self.ws / 'nowhere'), str(link))
        with self.assertRaisesRegex(S.SetupError, 'fails its pins .*move it aside, then rerun bootstrap'):
            self.bootstrap()
        self.assertTrue(link.is_symlink())
        self.assertEqual(self.calls, [])


if __name__ == '__main__':
    unittest.main()
