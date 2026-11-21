"""Native-only cold-library execution and refusal; real firmware sources, local simulator only."""
import hashlib
from pathlib import Path
import shutil
import tempfile
import unittest
import zlib
from xy_forever import native_json as J, sim, usb_proto as P
import test_simulator as S


@unittest.skipIf(S.UNAVAILABLE, S.UNAVAILABLE)
class NativePackage(unittest.TestCase):
    setUpClass = classmethod(S.SimulatorTest.setUpClass.__func__)
    tearDownClass = classmethod(S.SimulatorTest.tearDownClass.__func__)
    tearDown = S.SimulatorTest.tearDown
    connect = S.SimulatorTest.connect
    wait = S.SimulatorTest.wait
    menu_catalog = S.SimulatorTest.menu_catalog
    library_boot = S.SimulatorTest.library_boot

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(dir=self.tmp.name))
        self.root = self.dir / 'yaffs2'
        shutil.copytree(self.seeded, self.root)
        self.entry = self.root / 'xy-forever/firmware/opxy-121-stock'
        image = (self.entry / 'os.ldr').read_bytes()
        self.raw = J.package_document(dict(type='firmware', name='Native stock', version='native-v1', kind='official',
                                          bytes=len(image), crc32='%08x' % (zlib.crc32(image) & 0xffffffff),
                                          sha256=hashlib.sha256(image).hexdigest()))
        (self.entry / 'manifest.json').write_bytes(self.raw)
        (self.entry / 'info.txt').unlink()
        self.links = []
        self.start()

    def start(self):
        self.sim = sim.Simulator(self.root, self.dir / 'bootpart.bin', build_dir=self.out,
                                 log=self.dir / 'log.txt').start()

    def test_json_only_firmware_runs_through_native_library_boot(self):
        dev = self.connect(startup_ready=True)
        row = {r['id']: r for r in self.menu_catalog(dev)['items']}['opxy-121-stock']
        self.assertEqual((row['name'], row['version'], row['usable']), ('Native stock', 'native-v1', True))
        self.assertFalse(row['invalid_record'])
        self.assertEqual(self.library_boot(dev, 'opxy-121-stock'), (0, b''))
        self.wait(lambda status: status['a'] == 'os' and status['b'] == 'os', what='native-only entry execution')
        self.assertEqual((self.entry / 'manifest.json').read_bytes(), self.raw)
        self.assertFalse((self.entry / 'info.txt').exists())

    def test_bad_json_blocks_valid_legacy_without_boot_migration(self):
        for raw in (b'', b'{', b'{}', self.raw.replace(b'"schema_version":1', b'"schema_version":2')):
            with self.subTest(raw=raw):
                self.sim.stop()
                (self.entry / 'manifest.json').write_bytes(raw)
                (self.entry / 'info.txt').write_bytes(b'name=Legacy\nkind=official\n')
                self.start()
                dev = self.connect(startup_ready=True)
                row = {r['id']: r for r in self.menu_catalog(dev)['items']}['opxy-121-stock']
                self.assertEqual((row['usable'], row['invalid_record']), (False, True))
                self.assertEqual(self.library_boot(dev, 'opxy-121-stock')[0], P.ids.S_STATE)
                self.assertEqual((self.entry / 'manifest.json').read_bytes(), raw)
                self.assertEqual((self.entry / 'info.txt').read_bytes(), b'name=Legacy\nkind=official\n')
