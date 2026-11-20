"""The Info page redraws when the image's hash settles while it is open (key_ui usb_view_changed; image_label's
pointer changes then), in the simulator: its menu loop logs "menu: info image: ..." at each Info redraw, as key_ui
draws it. The keys come as the PSoC sends them (control "realkey"), so no HELLO completes the hash early.
Needs clang++ and the private stock 1.2.1 images (skipped without them). No device access."""
import hashlib
from pathlib import Path
import struct
import sys
import tempfile
import time
import unittest
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from xy_forever import sim                                                 # noqa: E402

UNAVAILABLE = sim.available() or (None if sim.STOCK_OS.is_file() else 'private stock 1.2.1 os.ldr absent')
PLAY, MINUS = 51, 53                                                       # xyf_menu.hpp keys
MARK = 'menu: info image: '


def boot_partition(size):
    """A boot partition holding a stand-in XY Forever os.ldr of size bytes (the LDR first record, flags 0x5004,
    argument size - 16; the cold descriptor at its end), and that image."""
    head = bytearray(struct.pack('<4I', 0xad005004, 0, 0, size - 16))
    for byte in bytes(head):
        head[2] ^= byte                                                    # the header's 16 bytes XOR to 0
    data = bytes(head) + bytes(i * 7 & 0xff for i in range(size - 16 - 8)) + struct.pack('<2I', 0x584c4348, 1)
    part = bytearray(0x400000 * 2)
    part[:13] = struct.pack('<3IB', 3, len(data), zlib.crc32(data), 1)
    part[512:512 + len(data)] = data
    return bytes(part), data


@unittest.skipIf(UNAVAILABLE, UNAVAILABLE)
class InfoImage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='xyf-sim-')
        self.dir = Path(self.tmp.name)
        sim.seed(self.dir / 'yaffs2')
        part, self.data = boot_partition(3 * 1024 * 1024)                 # a large image: about 0.8 s of hashing
        (self.dir / 'bootpart.bin').write_bytes(part)
        self.sim = sim.Simulator(self.dir / 'yaffs2', self.dir / 'bootpart.bin', build_dir=sim.build(),
                                 log=self.dir / 'log.txt').start()

    def tearDown(self):
        self.sim.stop()
        self.tmp.cleanup()

    def drawn(self):
        """The image line of every Info redraw so far."""
        log = (self.dir / 'log.txt').read_text(errors='replace') if (self.dir / 'log.txt').exists() else ''
        return [line[line.find(MARK) + len(MARK):].rsplit(' at ', 1)[0] for line in log.splitlines() if MARK in line]

    def test_info_redraws_when_the_hash_settles(self):
        for key in (MINUS, PLAY, MINUS, PLAY):                             # Settings (the last row), its About (#21)
            for pressed in (1, 0):
                self.assertTrue(self.sim.control('realkey %d %d' % (key, pressed)).get('ok'))
        image = 'image ' + hashlib.sha256(self.data).hexdigest()[:8]
        deadline = time.monotonic() + 30
        while image not in self.drawn() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertEqual(self.drawn(), ['image ...', image])              # opened while hashing, then redrawn once


if __name__ == '__main__':
    unittest.main()
