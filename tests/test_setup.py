"""scripts/setup.py with synthetic stand-ins for the stock files (never the real bytes): the pin check, the 1.2.5
refusal, unknown files, a copied /upgrade/ tree as input, and where the private folder comes from
(XYF_PRIVATE > xyf.local.json > ../private)."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

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

    def test_local_config_is_gitignored(self):
        self.assertIn(paths.LOCAL_CONFIG.name, (ROOT / '.gitignore').read_text().split())


if __name__ == '__main__':
    unittest.main()
