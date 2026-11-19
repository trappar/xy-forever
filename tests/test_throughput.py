"""scripts/throughput.py without a device: sizes, route choice, the table, the measure-and-clean-up loop over
fakes (an MTP route over test_mtp_write's fake OP-XY), and, when the simulator can run, both --sim routes
(skipped without clang++ or the private stock 1.2.1 images). No device access."""
import contextlib
import importlib.util
import io
from pathlib import Path
import re
import shutil
import sys
import tempfile
import unittest
import wave

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


NAME = r"[\w\d!+\-@\[\] #-)]+"                                     # the OS's allowlist name (smx_if.cpp)
OS_ALLOWED = re.compile(r'^/samples/%s(/%s)?$|^/samples/%s(/%s){0,2}\.[wW][aA][vV]$' % ((NAME,) * 4))
OS_TREE = dict(R.STOCK_TREE, **{'/samples/user/kick.wav': b'RIFF mine', '/projects/song.xy': b'mine'})


class OsMtp(unittest.TestCase):
    """os-mtp: the set as .wav files in /samples/throughput-scratch (all on the OS's allowlist), and cleanup
    removing exactly what this run made."""

    def test_the_wav_set(self):
        files = T.file_set(5 << 20, 1, 4096, 3)
        wavs = T.os_file_set(files)
        self.assertEqual([(n, len(d), s) for n, d, s in wavs],
                         [('big-0.wav', 5 << 20, False), ('small/s-000.wav', 4096, True),
                          ('small/s-001.wav', 4096, True), ('small/s-002.wav', 4096, True)])
        for (_, data, _), (_, wav_data, _) in zip(files, wavs):
            with wave.open(io.BytesIO(wav_data)) as w:
                self.assertEqual((w.getnchannels(), w.getsampwidth(), w.getframerate()), (2, 2, 44100))
                self.assertEqual(w.readframes(w.getnframes()), data[T.WAV_HEADER:])
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            T.main(['--route', 'os-mtp', '--small', '40'])                     # refused before any device

    def test_over_a_fake_os(self):
        device = TW.WritableFake(OS_TREE)
        before = device.tree()
        r = T.measure('os-mtp', T.OsMtpOps(device), T.os_file_set(T.file_set(70000, 1, 600, 5)))
        self.assertIsNone(r.error, r.error)
        self.assertEqual(r.removed[-1], T.OS_SCRATCH)
        self.assertEqual(len(r.removed), 6 + 2)
        for path in r.removed:
            self.assertRegex(path[len('/yaffs2'):], OS_ALLOWED)
        self.assertEqual(device.tree(), before)
        self.assertIn('removed /yaffs2/samples/throughput-scratch (8 paths', T.cleanup_lines(r)[0])

    def test_a_refused_file_cleans_up_what_was_made(self):
        device = TW.WritableFake(OS_TREE, deny={'s-002.wav'})
        before = device.tree()
        r = T.measure('os-mtp', T.OsMtpOps(device), T.os_file_set(T.file_set(1000, 1, 600, 5)))
        self.assertIn('SendObjectInfo /yaffs2/samples/throughput-scratch/small/s-002.wav', r.error)
        self.assertEqual(len(r.removed), 3 + 2)                            # big-0, s-000, s-001, small/, the folder
        self.assertIsNone(r.cleanup_error)
        self.assertEqual(device.tree(), before)

    def test_a_refused_folder_sends_nothing_more(self):
        device = TW.WritableFake(OS_TREE, deny={'throughput-scratch'})
        before = device.tree()
        r = T.measure('os-mtp', T.OsMtpOps(device), T.os_file_set(T.file_set(1000, 1, 600, 2)))
        self.assertIn('SendObjectInfo /yaffs2/samples/throughput-scratch: response', r.error)
        self.assertEqual(device.codes[-1], 0x100C)                         # no request after the refusal
        self.assertEqual((r.removed, r.cleanup_error), ([], None))
        self.assertEqual(device.tree(), before)

    def test_an_existing_scratch_folder_is_left_alone(self):
        device = TW.WritableFake(dict(OS_TREE, **{'/samples/throughput-scratch/mine.wav': b'RIFF'}))
        before = device.tree()
        r = T.measure('os-mtp', T.OsMtpOps(device), T.os_file_set(T.file_set(1000, 1, 600, 2)))
        self.assertIn('already on the OP-XY', r.error)
        self.assertFalse({0x100B, 0x100C, 0x100D} & set(device.codes))     # no write, no delete
        self.assertEqual(r.removed, [])
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
