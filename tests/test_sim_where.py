"""#114: B's where codes in the simulator. sim/devices.cpp logs "menu: where W1 shown" where key_ui would paint a
wait's code in the corner (past where::paint_ms while the menu has the screen) and "menu: where W1 cleared" where
it redraws without it. A forced slow A (control a_slow_ms) holds B in the file client's answer wait (W1) past
500 ms: the code shows, then clears; normal requests show nothing. Needs clang++ and the private stock 1.2.1 images
(skipped without them). No device access."""
from pathlib import Path
import re
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from xy_forever import sim                                                 # noqa: E402
import xyf_host as host                                                    # noqa: E402

UNAVAILABLE = sim.available() or (None if sim.STOCK_OS.is_file() else 'private stock 1.2.1 os.ldr absent')
PATH = '/yaffs2/xy-forever/state-a.txt'


@unittest.skipIf(UNAVAILABLE, UNAVAILABLE)
class WhereCodes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='xyf-sim-')
        self.dir = Path(self.tmp.name)
        sim.seed(self.dir / 'yaffs2')
        self.sim = sim.Simulator(self.dir / 'yaffs2', self.dir / 'bootpart.bin', build_dir=sim.build(),
                                 log=self.dir / 'log.txt').start()
        self.dev = host.Device(host.TcpPort(self.sim.address), on_log=None)
        self.assertEqual(self.dev.hello()[0], 0)

    def tearDown(self):
        self.dev.t.close()
        self.sim.stop()
        self.tmp.cleanup()

    def where(self):
        """The logged corner changes in order: ('shown' | 'cleared', code)."""
        log = (self.dir / 'log.txt').read_text(errors='replace') if (self.dir / 'log.txt').exists() else ''
        return [(m.group(2), int(m.group(1))) for m in re.finditer(r'menu: where W(\d+) (shown|cleared) at', log)]

    def test_a_short_wait_paints_nothing(self):
        for _ in range(3):
            self.assertEqual(self.dev.read_file(PATH)[0], 0)
        self.sim.advance(200)
        self.assertEqual(self.where(), [])

    def test_a_long_answer_wait_shows_its_code_then_clears(self):
        self.assertEqual(self.dev.read_file(PATH)[0], 0)                    # mounted: the next read is file requests only
        self.sim.set('a_slow_ms', 800)                                       # each file request answered 800 ms late
        try:
            self.assertEqual(self.dev.read_file(PATH)[0], 0)
        finally:
            self.sim.set('a_slow_ms', 0)
        self.sim.advance(1500)                                               # the read's close, still late, ends
        seen = self.where()
        self.assertTrue(seen, 'no where code logged')
        self.assertEqual(set(seen), {('shown', 1), ('cleared', 1)})          # W1: file_client await_answer
        self.assertEqual(seen[::2], [('shown', 1)] * (len(seen) // 2))       # each shown once, then cleared
        self.assertEqual(seen[1::2], [('cleared', 1)] * (len(seen) // 2))
        self.assertEqual(self.dev.read_file(PATH)[0], 0)                     # fast again: nothing more
        self.sim.advance(200)
        self.assertEqual(self.where(), seen)


if __name__ == '__main__':
    unittest.main()
