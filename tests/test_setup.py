"""scripts/setup.py with synthetic stand-ins for the stock files (never the real bytes): the pin check, the 1.2.5
refusal, unknown files, a copied /upgrade/ tree as input, and where the private folder comes from
(XYF_PRIVATE > xyf.local.json > ../private), the Python check (3.9+), and `bootstrap` in a temporary workspace
with the toolchain download (a synthetic zip) and npm stubbed (no network)."""
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
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from xy_forever import modlink, node_deps, outer, paths, usb_proto  # noqa: E402

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
        with mock.patch.object(S.paths, 'STOCK_121', self.dest), \
                mock.patch.object(S.paths, 'CATALOG_121', self.dest / 'op1_factory.json'), \
                mock.patch.object(S, 'git_show', lambda path: shown.append(path) or served), \
                contextlib.redirect_stdout(out):
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
        self.assertEqual(S.PINS['stock']['1.2.1'], {'os.ldr': usb_proto.STOCK_OS_LDR_SHA256,
                                                    'bootloader.ldr': outer.BOOT_HASH})
        self.assertEqual(S.identify('os.ldr', usb_proto.STOCK_OS_LDR_SHA256), '1.2.1')
        self.assertEqual(S.identify('bootloader.ldr', usb_proto.STOCK_OS_LDR_SHA256), None)
        self.assertEqual(set(S.PINS['stock']), {'1.2.1', '1.2.5'})

    def test_open_xy_pin_is_the_mod_sdk(self):
        self.assertEqual(S.PINS['open_xy']['sdk_commit'], modlink.SDK_COMMIT)
        self.assertEqual(paths.PUBLISHED_MODS.parent.name, 'opxy-1.2.1')


class PythonVersion(unittest.TestCase):
    def test_39_or_newer_runs(self):
        for v in ((3, 9, 6), (3, 10, 14), (3, 11, 0), (3, 12, 4), (4, 0, 0)):
            self.assertIsNone(S.python_problem(v))
            S.require_python(v)

    def test_older_than_39_is_refused_before_doing_anything(self):
        self.assertRegex(S.python_problem((3, 8, 10)), r'3\.8\.10 is too old: XY Forever needs 3\.9\+.*python3\.12')
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


def toolchain_zip(entries):
    """A zip of {name: (bytes, mode)} like open-xy's release (unix modes in the high bits of external_attr)."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as z:
        for name, (data, mode) in entries.items():
            info = zipfile.ZipInfo(name)
            info.external_attr = mode << 16
            z.writestr(info, data)
    return out.getvalue()


FOLDER = 'open-xy-toolchain-darwin-arm64'
GOOD_ZIP = toolchain_zip({FOLDER + '/': (b'', 0o40755), FOLDER + '/SOURCES.md': (b'sources\n', 0o100644),
                          FOLDER + '/bin/bfin-elf-as': (b'as', 0o100755),
                          FOLDER + '/lib/gcc/bfin-elf/8.0.0/libgcc.a': (b'lib', 0o100644)})


class ToolchainPins(unittest.TestCase):
    def test_each_downloadable_host_has_a_pinned_release_archive(self):
        pins = S.PINS['toolchain']
        hosts = json.loads((ROOT / 'toolchains.json').read_text())['hosts']
        self.assertEqual(set(pins['archives']), set(hosts) - {'linux-x86_64'})
        for host, record in pins['archives'].items():
            self.assertEqual(record['url'], '%s/open-xy-toolchain-%s.zip'
                             % (pins['release'].replace('/tag/', '/download/'), host))
            self.assertEqual(record['folder'], 'open-xy-toolchain-' + host)
            self.assertRegex(record['sha256'], r'^[0-9a-f]{64}$')


class Bootstrap(unittest.TestCase):
    """bootstrap in a temporary workspace: the toolchain download serves a synthetic zip and npm ci is stubbed."""

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
        self.calls, self.stock, self.which, self.served = [], False, {'node', 'npm'}, GOOD_ZIP
        self.record = {'url': 'https://example.invalid/%s.zip' % FOLDER, 'folder': FOLDER,
                       'sha256': hashlib.sha256(GOOD_ZIP).hexdigest()}
        for name, value in (('run', self.fake_run), ('toolchain_problem', self.fake_toolchain),
                            ('stock_present', lambda: self.stock), ('cmd_doctor', lambda args: 1),
                            ('cmd_fixtures', lambda args: self.calls.append('fixtures')),
                            ('fixtures_missing', lambda: ['verified build']), ('host_name', lambda: 'darwin-arm64'),
                            ('download', self.fake_download)):
            patcher = mock.patch.object(S, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for patcher in (mock.patch.object(S.shutil, 'which', self.fake_which),
                        mock.patch.dict(S.PINS, {'toolchain': {'archives': {'darwin-arm64': self.record}}})):
            patcher.start()
            self.addCleanup(patcher.stop)

    def fake_run(self, cmd, cwd=None):
        self.calls.append(' '.join(map(str, cmd[:2])))
        (Path(cwd) / 'node_modules').mkdir()

    def fake_download(self, url, path):
        self.calls.append('download ' + url)
        Path(path).write_bytes(self.served)

    def fake_which(self, name):
        return '/bin/' + name if name in self.which else None

    def fake_toolchain(self, directory=None):        # stands in for the per-file check against toolchains.json
        return None if directory and (Path(directory) / 'bin/bfin-elf-as').is_file() else 'missing'

    def bootstrap(self, dry_run=False, workspace=None, environ=None, archive=None):
        out = io.StringIO()
        b = S.Bootstrap(workspace or self.ws, self.repo, self.config, dry_run, environ or {}, archive)
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
        self.assertEqual((self.repo / 'docs/status/README.md').read_text(), '# status\n')
        self.assertEqual(json.loads(self.config.read_text()), {'leak_patterns': []})   # ../private: nothing recorded
        tools = self.ws / 'tools/blackfin'
        self.assertFalse(tools.is_symlink())
        self.assertEqual((tools / 'bin/bfin-elf-as').read_bytes(), b'as')
        self.assertEqual(sorted(p.name for p in (self.ws / 'tools').iterdir()), ['blackfin'])   # no temporary left
        self.assertEqual(self.calls, ['download ' + self.record['url'], '/bin/npm ci', '/bin/npm ci'])
        self.assertEqual((self.ws / 'CLAUDE.md').read_text(), '@AGENTS.md\n')
        guide = (self.ws / 'AGENTS.md').read_text()
        self.assertIn('Read `xy-forever/AGENTS.md` first and follow it', guide)
        self.assertIn('"Continue development" works from here', guide)
        self.assertTrue(10 <= len(guide.splitlines()) <= 15)
        self.assertRegex(out, r'Next:\n  1\. Put your OP-XY .* in MTP mode: hold COM .*\n  2\. python3 scripts/setup\.py '
                         r'import-stock --from-device +\(reads them into %s.*/upgrade/firmware/blackfin/bin/os\.ldr'
                         % (self.ws / 'private/incoming'))
        self.assertIn('import-stock %s' % (self.ws / 'private/incoming'), out)
        self.assertIn('4. Open your agent in %s (or %s) and say "Continue development"' % (self.ws, self.repo), out)
        before, self.calls = self.tree(), []
        b, out = self.bootstrap()
        self.assertEqual((b.actions, self.calls, self.tree()), ([], [], before))
        self.assertIn('nothing to do: the workspace is already set up; nothing changed', out)

    def test_an_older_single_status_file_becomes_the_folder_index(self):
        (self.repo / 'docs/STATUS.md').write_text('# mine\n')
        b, out = self.bootstrap()
        self.assertEqual((self.repo / 'docs/status/README.md').read_text(), '# mine\n')
        self.assertFalse((self.repo / 'docs/STATUS.md').exists())
        self.assertIn('move docs/STATUS.md to docs/status/README.md', out)

    @unittest.skipIf(sys.platform == 'win32', 'no unix permission bits')
    def test_executable_bits_come_back_from_the_zip(self):
        self.bootstrap()
        tools = self.ws / 'tools/blackfin'
        self.assertTrue(S.os.access(str(tools / 'bin/bfin-elf-as'), S.os.X_OK))
        for name in ('SOURCES.md', 'lib/gcc/bfin-elf/8.0.0/libgcc.a'):
            self.assertFalse(S.os.access(str(tools / name), S.os.X_OK), name)

    def test_dry_run_prints_every_action_and_changes_nothing(self):
        before = self.tree()
        b, out = self.bootstrap(dry_run=True)
        self.assertEqual((self.calls, self.tree()), ([], before))
        for what in ('create 12 folders', 'copy docs/STATUS.template.md to docs/status/README.md', '"leak_patterns"',
                     'write %s' % (self.ws / 'AGENTS.md'), 'write %s' % (self.ws / 'CLAUDE.md'),
                     'download %s, check its SHA-256' % self.record['url'], 'npm ci in clients/ts', 'npm ci in web'):
            self.assertRegex(out, r'  would .*' + re.escape(what))
        self.assertIn('dry run: the 8 actions above would run; nothing changed', out)

    def test_an_archive_with_the_wrong_hash_is_refused_and_nothing_stays(self):
        self.served = GOOD_ZIP + b'tampered'
        with self.assertRaisesRegex(S.SetupError, 'toolchain archive hash mismatch: .* the pin \\(stock.json\\) is %s'
                                    % self.record['sha256']):
            self.bootstrap()
        self.assertTrue((self.repo / 'docs/status/README.md').exists())   # what came before stays done
        self.assertEqual(list((self.ws / 'tools').iterdir()), [])

    def test_a_given_archive_is_used_without_a_download(self):
        archive = self.ws / 'downloaded.zip'
        archive.write_bytes(GOOD_ZIP)
        b, out = self.bootstrap(archive=str(archive))
        self.assertNotIn('download', ' '.join(self.calls))
        self.assertIn('%s (given), check its SHA-256' % archive, out)
        self.assertTrue((self.ws / 'tools/blackfin/bin/bfin-elf-as').is_file())
        self.assertTrue(archive.is_file())                              # the developer's file is kept

    def test_an_unpacked_toolchain_failing_its_pins_or_an_escaping_entry_is_refused(self):
        for entries, message in (({FOLDER + '/SOURCES.md': (b'x', 0o100644)}, 'fails its pins \\(toolchains.json\\)'),
                                 ({FOLDER + '/../evil': (b'x', 0o100644)}, 'unexpected entry')):
            self.served = toolchain_zip(entries)
            self.record['sha256'] = hashlib.sha256(self.served).hexdigest()
            with self.assertRaisesRegex(S.SetupError, message):
                self.bootstrap()
            self.assertEqual(list((self.ws / 'tools').iterdir()), [])
            self.assertFalse((self.ws / 'evil').exists())

    def test_a_failed_download_names_the_manual_route(self):
        def offline(url, path):
            raise S.urllib.error.URLError('no network')
        with mock.patch.object(S, 'download', offline), self.assertRaisesRegex(
                S.SetupError, 'could not download .*in a browser, then run .*--toolchain-archive'):
            self.bootstrap()

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
        self.which = set()
        b, out = self.bootstrap()
        self.assertNotIn('/bin/npm ci', self.calls)
        self.assertIn('clients/ts: skipped: Node not found', out)

    def test_another_workspace_is_recorded_unless_the_private_folder_is_set(self):
        other = self.ws.parent / 'other'
        with mock.patch.object(S, 'host_name', lambda: 'linux-x86_64'):
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
        self.assertRegex(out, r'Next:\n  1\. Open your agent in ')

    def test_an_existing_toolchain_folder_or_link_that_fails_its_pins_is_left_alone(self):
        tools = self.ws / 'tools/blackfin'
        (tools / 'bin').mkdir(parents=True)
        (tools / 'bin/old').write_bytes(b'mine')
        with self.assertRaisesRegex(S.SetupError, 'exists but fails its pins .*move it aside, then rerun bootstrap'):
            self.bootstrap()
        self.assertEqual((tools / 'bin/old').read_bytes(), b'mine')
        S.os.rename(str(tools), str(self.ws / 'aside'))
        S.os.symlink(str(self.ws / 'nowhere'), str(tools))
        with self.assertRaisesRegex(S.SetupError, 'exists but fails its pins'):
            self.bootstrap()
        self.assertTrue(tools.is_symlink())
        self.assertEqual(self.calls, [])

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



class DoctorNodeRows(unittest.TestCase):
    """doctor's node rows: one for clients/ts and one for web, each ready once its node_modules tools are."""

    def rows(self, root, node='/bin/node'):
        version = mock.Mock(stdout='v24.0.0\n')
        with mock.patch.object(node_deps.shutil, 'which', lambda name: node), \
                mock.patch.object(node_deps.subprocess, 'run', return_value=version):
            return node_deps.doctor_rows(root)

    def test_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(self.rows(root), [
                ('node (clients/ts)', False, 'v24.0.0; run `npm ci` in clients/ts'),
                ('node (web)', False, 'v24.0.0; run `npm ci` in web')])
            for rel in ('clients/ts/node_modules/.bin/tsc', 'web/node_modules/.bin/tsc'):
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                (root / rel).touch()
            self.assertEqual([r[1:] for r in self.rows(root)],
                             [(True, 'v24.0.0; node_modules ok'), (False, 'v24.0.0; run `npm ci` in web')])
            (root / 'web/node_modules/.bin/vitest').touch()
            self.assertEqual([r[1] for r in self.rows(root)], [True, True])
            self.assertEqual([r[1:] for r in self.rows(root, node=None)],
                             [(False, 'not found (Node 22.18+ or 23.6+); node_modules ok')] * 2)

    def test_doctor_reports_them(self):
        self.assertEqual([r[0] for r in S.doctor()[0] if r[0].startswith('node')], ['node (clients/ts)', 'node (web)'])


if __name__ == '__main__':
    unittest.main()
