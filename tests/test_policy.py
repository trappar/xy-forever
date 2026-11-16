import ctypes
from pathlib import Path
import sys
import tempfile
import unittest
from xy_forever.packages import destination_for

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from xy_forever import hostbuild  # noqa: E402
ROLES=('os','backplane','ble','metadata','content','excluded-bootloader','excluded-host-metadata')

class NativePolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory(prefix='xyf-policy-')
        path=Path(cls.tmp.name)/'policy.dylib'
        hostbuild.run(['/usr/bin/clang++','-std=c++11','-Wall','-Wextra','-Werror','-shared','-fPIC',
                       str(ROOT/'native/package_policy.cpp'),str(ROOT/'tests/policy_bridge.cpp'),'-o',str(path)],check=True)
        cls.lib=ctypes.CDLL(str(path))
        cls.lib.package_destination.argtypes=[ctypes.c_void_p,ctypes.c_uint,ctypes.c_void_p,ctypes.c_void_p]
        cls.lib.package_version.argtypes=[ctypes.c_void_p,ctypes.c_uint,ctypes.c_void_p]

    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()

    def classify(self,name):
        raw=name.encode('utf-8');role=ctypes.c_uint();path=ctypes.create_string_buffer(257)
        status=self.lib.package_destination(raw,len(raw),ctypes.byref(role),path)
        return status,ROLES[role.value],path.value.decode('ascii') or None

    def test_host_and_native_component_rules_agree(self):
        paths=['firmware/blackfin/bin/os.ldr','firmware/backplane/bin/backplane.cyacd',
               'firmware/ble/bin/ble.bin','firmware/blackfin/bin/os.version',
               'firmware/backplane/bin/backplane.version','firmware/ble/bin/ble.version',
               'firmware/firmware.version','firmware/versions.txt',
               'firmware/blackfin/content/presets/custom/default.json',
               'firmware/blackfin/bin/bootloader.ldr','firmware/ble/bin/nordic_bootloader_0.bin',
               'firmware/blackfin/content/BoOtLoAdEr/future.bin','__MACOSX/x','firmware/.DS_Store']
        for name in paths:
            with self.subTest(name=name):
                status,role,destination=self.classify(name)
                self.assertEqual(status,0)
                self.assertEqual((role,destination),destination_for(name))
                self.assertNotEqual(destination,'os.ldr')

    def test_unknown_paths_and_aliases_never_get_a_destination(self):
        for name in ('/os.ldr','os.ldr','firmware/new-mcu/bin/foo.bin',
                     'firmware/blackfin/content/../os.ldr','a//b','a/./b','a/b.',
                     'firmware/blackfin/content/evil\x00.ldr','a\\b','C:foo','é'):
            with self.subTest(name=name):
                status,_,destination=self.classify(name)
                self.assertNotEqual(status,0)
                self.assertIsNone(destination)

    def test_stock_version_formats_and_malformed_numbers(self):
        for raw in (b'1.2.5+0',b'1.2.5+0\n',b'1.2.5+0@'+b'a'*40+b'\r\n'):
            fields=(ctypes.c_uint*4)()
            self.assertEqual(self.lib.package_version(raw,len(raw),fields),0)
            self.assertEqual(list(fields),[1,2,5,0])
        for raw in (b'',b'1.2.5',b'1.2.5+65536',b'1.2.5+0garbage',b'1.2.5+0@bad',b'1.2.5+-1'):
            fields=(ctypes.c_uint*4)(99,99,99,99)
            self.assertNotEqual(self.lib.package_version(raw,len(raw),fields),0)
            self.assertEqual(list(fields),[99]*4)
