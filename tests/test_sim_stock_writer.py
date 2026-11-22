"""Stock-writer integration through the unchanged shipping B INSTALL/UNINSTALL caller."""

import hashlib
import struct
import unittest
import zlib

import pytest
import test_simulator as support
from test_simulator import BUILD, ids, sim

KEY = bytes((0xA5 + i * 29) & 255 for i in range(256))  # the representation's period: 256 bytes


def represented(data):
    key = (KEY * (len(data) // 256 + 1))[: len(data)]
    return (int.from_bytes(data, 'little') ^ int.from_bytes(key, 'little')).to_bytes(len(data), 'little')


@unittest.skipIf(sim.available() or not sim.STOCK_OS.is_file(), 'simulator fixtures unavailable')
class StockWriterIntegration(unittest.TestCase):
    setUpClass = classmethod(support.SimulatorTest.setUpClass.__func__)
    tearDownClass = classmethod(support.SimulatorTest.tearDownClass.__func__)
    setUp = support.SimulatorTest.setUp
    tearDown = support.SimulatorTest.tearDown
    connect = support.SimulatorTest.connect
    wait = support.SimulatorTest.wait

    @pytest.mark.sim
    def test_install_and_uninstall_write_the_boot_partition(self):
        """Kept: The boot partition writer installs and uninstalls with read-back."""
        dev = self.connect(startup_ready=True)  # this case begins with a staged mutation
        stock = sim.STOCK_OS.read_bytes()
        self.assertEqual(dev.stage(stock), 0)
        code, written, partition, sectors = dev.uninstall(hashlib.sha256(stock).digest())
        self.assertEqual((code, written, partition, sectors), (0, len(stock), 1, len(stock) // 512 + 1))
        raw = (self.dir / 'bootpart.bin').read_bytes()
        self.assertEqual(struct.unpack_from('<3I', raw), (3, len(stock), zlib.crc32(stock)))
        self.assertEqual(raw[512 : 512 + len(stock)], represented(stock))
        self.assertEqual(raw[12:22], b'\x01\x01XYFSIM01')
        self.assertEqual(dev.uninstall(hashlib.sha256(stock).digest())[0], ids.S_STATE)  # staging used up
        if BUILD:  # the second slot, the other erased
            data = (BUILD / 'candidate.ldr').read_bytes()
            self.assertEqual(dev.stage(data), 0)
            self.assertEqual(dev.install(hashlib.sha256(data).digest())[:3], (0, len(data), 2))
            raw = (self.dir / 'bootpart.bin').read_bytes()
            self.assertEqual(raw[:512], bytes(512))
            self.assertEqual(struct.unpack_from('<3I', raw, 0x400000), (3, len(data), zlib.crc32(data)))
        self.assertEqual(dev.stage(stock), 0)
        self.assertEqual(dev.install(hashlib.sha256(stock).digest())[0], ids.S_IMAGE)  # no cold descriptor
