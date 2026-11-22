"""Stock-writer integration through the unchanged shipping B INSTALL/UNINSTALL caller."""
import hashlib
import struct
import zlib
import unittest

import pytest
import test_simulator as support
from test_simulator import bounds, sim, BUILD, ids, BASE


KEY = bytes((0xa5 + i * 29) & 255 for i in range(256))                   # the representation's period: 256 bytes


def represented(data):
    key = (KEY * (len(data) // 256 + 1))[:len(data)]
    return (int.from_bytes(data, 'little') ^ int.from_bytes(key, 'little')).to_bytes(len(data), 'little')


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

    @pytest.mark.sim
    def test_install_and_uninstall_write_the_boot_partition(self):
        """Kept: The boot partition writer installs and uninstalls with read-back."""
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

    @pytest.mark.sweep
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

    @pytest.mark.sweep
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
