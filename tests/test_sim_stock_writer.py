"""Stock-writer integration through the unchanged shipping B INSTALL/UNINSTALL caller."""
import hashlib
import struct
import zlib
import unittest
import test_simulator as support
from test_simulator import sim, BUILD, ids, BASE, P


def represented(data):
    return bytes(b ^ ((0xa5 + i * 29) & 255) for i, b in enumerate(data))


@unittest.skipIf(sim.available() or not sim.STOCK_OS.is_file(), 'simulator fixtures unavailable')
class StockWriterIntegration(unittest.TestCase):
    setUpClass = classmethod(support.SimulatorTest.setUpClass.__func__)
    tearDownClass = classmethod(support.SimulatorTest.tearDownClass.__func__)
    setUp = support.SimulatorTest.setUp
    tearDown = support.SimulatorTest.tearDown
    connect = support.SimulatorTest.connect
    write = support.SimulatorTest.write
    file = support.SimulatorTest.file
    wait = support.SimulatorTest.wait

    def test_read_files_after_verified_uninstall_in_same_session(self):
        stock = sim.STOCK_OS.read_bytes()
        root = self.dir / 'yaffs2'
        (root / 'uninstall-probe.txt').write_bytes(b'user-area file')
        dev = self.connect(startup_ready=True)
        list_root = b'/yaffs2\0' + struct.pack('<I', 0)
        read = b'/yaffs2/uninstall-probe.txt\0'
        self.assertEqual(dev.call(P.LIST_DIR, list_root)[0], 0)
        self.assertEqual(dev.call(P.READ_FILE, read), (0, b'user-area file'))
        digest = hashlib.sha256(stock).digest()
        self.assertEqual(dev.stage(stock), 0)
        self.sim.set('fail_select', 1)
        self.assertEqual(dev.uninstall(digest)[0], ids.S_INSTALL)
        self.assertEqual(self.sim.status()['partition'], 0)
        self.assertEqual(dev.call(P.LIST_DIR, list_root)[0], 0)
        self.assertEqual(dev.stage(stock), 0)
        self.assertEqual(dev.uninstall(digest), (0, len(stock), 1, len(stock) // 512 + 1))
        self.assertEqual(self.sim.status()['partition'], 0)
        self.assertEqual(dev.call(P.LIST_DIR, list_root)[0], 0)
        self.assertEqual(dev.call(P.READ_FILE, read), (0, b'user-area file'))

    def test_install_and_uninstall_write_the_boot_partition(self):
        dev = self.connect(startup_ready=True)                          # this case begins with a staged mutation
        stock = sim.STOCK_OS.read_bytes()
        self.assertEqual(dev.stage(stock), 0)
        code, written, partition, sectors = dev.uninstall(hashlib.sha256(stock).digest())
        self.assertEqual((code, written, partition, sectors), (0, len(stock), 1, len(stock) // 512 + 1))
        raw = (self.dir / 'bootpart.bin').read_bytes()
        self.assertEqual(struct.unpack_from('<3I', raw), (3, len(stock), zlib.crc32(stock)))
        self.assertEqual(raw[512:512 + len(stock)], represented(stock))
        self.assertEqual(raw[12:22], b'\x01\x01XYFSIM01')
        self.assertEqual(dev.uninstall(hashlib.sha256(stock).digest())[0], ids.S_STATE)   # staging used up
        if BUILD:                                                         # the second slot, the other erased
            data = (BUILD / 'candidate.ldr').read_bytes()
            self.assertEqual(dev.stage(data), 0)
            self.assertEqual(dev.install(hashlib.sha256(data).digest())[:3], (0, len(data), 2))
            raw = (self.dir / 'bootpart.bin').read_bytes()
            self.assertEqual(raw[:512], bytes(512))
            self.assertEqual(struct.unpack_from('<3I', raw, 0x400000), (3, len(data), zlib.crc32(data)))
        self.assertEqual(dev.stage(stock), 0)
        self.assertEqual(dev.install(hashlib.sha256(stock).digest())[0], ids.S_IMAGE)    # no cold descriptor

    def test_transient_writer_error_retries_and_returns_to_user_partition(self):
        """Stock retries the failed trim; B still verifies encrypted staging and restores user media."""
        dev = self.connect(startup_ready=True)
        stock = sim.STOCK_OS.read_bytes()
        self.assertEqual(dev.stage(stock), 0)
        self.sim.set('fail_write', 1)                      # the writer's first boot-partition write fails
        code, written, partition, _ = dev.uninstall(hashlib.sha256(stock).digest())
        self.assertEqual((code, written, partition), (0, len(stock), 1))
        status = self.sim.status()
        self.assertEqual((status['partition'], status['fail_write']), (0, 0))
        data = b'written after a failed install'
        self.assertEqual(self.write(dev, data, BASE + 'log/after.bin'), (0, len(data), zlib.crc32(data)))
        self.assertEqual(self.file(BASE + 'log/after.bin'), data)
        self.assertEqual(self.sim.status()['boot_writes'], 0)

    def test_initial_select_error_is_nonzero_but_readback_refuses_it(self):
        dev = self.connect(startup_ready=True)
        stock = sim.STOCK_OS.read_bytes()
        self.assertEqual(dev.stage(stock), 0)
        self.sim.set('fail_select', 1)
        self.assertEqual(dev.uninstall(hashlib.sha256(stock).digest())[:3], (ids.S_INSTALL, 1, 0))
        self.assertEqual(self.sim.status()['partition'], 0)
        self.assertEqual(self.write(dev, b'after', BASE + 'log/after.bin')[:2], (0, 5))

    def test_readback_select_error_after_success_is_refused(self):
        dev = self.connect()
        stock = sim.STOCK_OS.read_bytes()
        digest = hashlib.sha256(stock).digest()
        self.assertEqual(dev.stage(stock), 0)
        self.assertEqual(dev.uninstall(digest), (0, len(stock), 1, len(stock) // 512 + 1))
        self.assertEqual(dev.stage(stock), 0)
        # The second write uses partition 2. Fail reselecting it after its header was found.
        self.sim.set('fail_select', 7)
        self.assertEqual(dev.uninstall(digest), (ids.S_INSTALL, len(stock), 2, 0))
        self.assertEqual(self.sim.status()['partition'], 0)

    def test_encrypted_install_boots_plaintext_for_identity(self):
        if not BUILD:
            self.skipTest('verified product build absent')
        dev = self.connect(startup_ready=True)
        data = (BUILD / 'candidate.ldr').read_bytes()
        self.assertEqual(dev.stage(data), 0)
        self.assertEqual(dev.install(hashlib.sha256(data).digest())[:3], (0, len(data), 1))
        image = 'image=' + hashlib.sha256(data).hexdigest()[:8]
        self.assertEqual(dev.call(P.REBOOT, struct.pack('<I', 2))[0], 0)
        self.wait(lambda s: s['b_boots'] >= 2 and s['listening'])
        self.assertIn(image, self.connect().hello()[1])
        self.sim.stop()
        self.sim = sim.Simulator(self.dir / 'yaffs2', self.dir / 'bootpart.bin', build_dir=self.out,
                                 log=self.dir / 'cold.log').start()
        self.assertIn(image, self.connect().hello()[1])
