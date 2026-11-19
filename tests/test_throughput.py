"""scripts/throughput.py without a device: sizes, route choice, the table, the measure-and-clean-up loop over
fakes (an MTP route over test_mtp_write's fake OP-XY), and, when the simulator can run, both --sim routes
(skipped without clang++ or the private stock 1.2.1 images). No device access."""
import contextlib
import importlib.util
import io
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
from xy_forever import paths, sim  # noqa: E402
import test_mtp_read as R  # noqa: E402
import test_mtp_write as TW  # noqa: E402

spec = importlib.util.spec_from_file_location('xyf_throughput', ROOT / 'scripts/throughput.py')
T = importlib.util.module_from_spec(spec)
spec.loader.exec_module(T)

UNAVAILABLE = sim.available() or (None if sim.STOCK_OS.is_file() else 'private stock 1.2.1 os.ldr absent')


class MemoryOps:
    """Ops over a dict; corrupt: a path whose read comes back wrong."""

    def __init__(self, corrupt=None, leftovers=()):
        self.files, self.corrupt, self.leftovers, self.cleaned = {}, corrupt, list(leftovers), 0

    def prepare(self):
        return self.leftovers

    def write(self, path, data):
        self.files[path] = data
        return path

    def read(self, token, path, size):
        return b'x' if path == self.corrupt else self.files[token]

    def cleanup(self):
        self.cleaned += 1
        removed, self.files = sorted(self.files), {}
        return removed


def ticks():
    t = [0.0]

    def clock():
        t[0] += 0.5
        return t[0]
    return clock


class Logic(unittest.TestCase):
    def test_sizes(self):
        self.assertEqual([T.parse_size(s) for s in ('5M', '4K', '512', '4KiB', '1m')],
                         [5 << 20, 4096, 512, 4096, 1 << 20])
        self.assertRaises(Exception, T.parse_size, '5G')

    def test_route_choice(self):
        self.assertEqual(T.select_routes(None, None), list(T.ROUTES))
        self.assertEqual(T.select_routes(None, '127.0.0.1:1'), ['serial', 'xyf-mtp'])
        self.assertEqual(T.select_routes('dev-mtp', None), ['dev-mtp'])
        self.assertEqual(T.select_routes('xyf-mtp', '127.0.0.1:1'), ['xyf-mtp'])
        for stock in ('os-mtp', 'dev-mtp'):
            with self.assertRaisesRegex(SystemExit, 'device only'):
                T.select_routes(stock, '127.0.0.1:1')
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            T.main(['--route', 'usb'])

    def test_file_set(self):
        files = T.file_set(1000, 2, 10, 3)
        self.assertEqual([(n, len(d), s) for n, d, s in files],
                         [('big-0.bin', 1000, False), ('big-1.bin', 1000, False), ('small/s-000.bin', 10, True),
                          ('small/s-001.bin', 10, True), ('small/s-002.bin', 10, True)])
        self.assertEqual(files, T.file_set(1000, 2, 10, 3))                # the same set for every route

    def test_measure_and_table(self):
        files = T.file_set(2048, 1, 1024, 4)
        ok = T.measure('serial', MemoryOps(leftovers=['/yaffs2/throughput-scratch/old']), files, ticks())
        self.assertIsNone(ok.error)
        self.assertEqual(ok.seconds, {(k, s): 0.5 for k in ('write', 'read') for s in (False, True)})
        self.assertEqual(len(ok.removed), 5)
        bad_ops = MemoryOps(corrupt=T.SCRATCH + '/small/s-001.bin')
        bad = T.measure('xyf-mtp', bad_ops, files, ticks())
        self.assertIn('small/s-001.bin read back differs', bad.error)
        self.assertEqual(bad_ops.cleaned, 1)                                # cleaned up after the failure
        text = T.table([ok, bad])
        lines = text.splitlines()
        self.assertEqual(lines[0].split()[0], 'route')
        self.assertEqual(lines[1].split(), ['serial', '4.0', '4.0', '8.0', '8.0', '5', '6', '2.00'])
        self.assertTrue(lines[2].startswith('xyf-mtp') and 'failed:' in lines[2])
        log = T.cleanup_lines(ok)
        self.assertIn('leftovers', log[0])
        self.assertIn('removed /yaffs2/throughput-scratch (5 paths', log[1])

    def test_an_mtp_route_over_a_fake_op_xy(self):
        device = TW.WritableFake(R.STOCK_TREE)
        before = device.tree()
        files = T.file_set(70000, 1, 600, 5)
        r = T.measure('dev-mtp', T.MtpOps(device), files)
        self.assertIsNone(r.error, r.error)
        self.assertEqual(r.removed[-1], T.SCRATCH)
        self.assertEqual(len(r.removed), 6 + 2)                            # 6 files, small/, the scratch folder
        self.assertEqual(device.tree(), before)


@unittest.skipIf(UNAVAILABLE, UNAVAILABLE)
class Simulator(unittest.TestCase):
    def test_both_sim_routes(self):
        paths.SESSIONS.mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix='sim-throughput-', dir=paths.SESSIONS))
        self.addCleanup(shutil.rmtree, work, True)
        root = sim.seed(work / 'yaffs2')
        s = sim.Simulator(root, work / 'bootpart.bin', log=work / 'log.txt').start()
        self.addCleanup(s.stop)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = T.main(['--sim', s.address, '--big', '20K', '--small', '1K', '--small-count', '4'])
        text = out.getvalue()
        self.assertEqual(code, 0, text)
        self.assertRegex(text, r'\nserial +[\d.]+ ')
        self.assertRegex(text, r'\nxyf-mtp +[\d.]+ ')
        self.assertFalse((root / 'throughput-scratch').exists())


if __name__ == '__main__':
    unittest.main()
