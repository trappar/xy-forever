"""In the simulator: the first start after Track 5 wrote XY Forever onto a stock OP-XY (#178). The library is empty, so
the device adopts the stock updater's own os.ldr (/yaffs2/upgrade/firmware/blackfin/bin/os.ldr): it hashes it (the
exact stock 1.2.1 image, else it writes nothing), copies it into tmp/, reads it back, writes manifest.json and files.sha256
(as `uv run xyf-host library-add` and a package publish), renames the folder in as library entry opxy-121, makes it the
default and converges (#197). A plain power-on then boots it; PLAY at power-on adopts and stays in the menu, and
job.status reports the adoption. A missing or wrong file refuses with a plain reason; a power cut mid-copy is redone at
the next start; a second start writes nothing. Needs clang++ and the private stock 1.2.1 images (skipped without
them). No device access."""

import hashlib
import shutil
import tempfile
import time
import unittest
import zlib
from pathlib import Path

import pytest
from test_sim_mod_job import UNAVAILABLE

from xy_forever import bounds, native_json, sim
from xy_forever import modsend as MS
from xy_forever import xyf_host as host

ENTRY = 'xy-forever/firmware/opxy-121'
STATE_FILE = '/yaffs2/xy-forever/state-a.txt'
REINSTALL = ' Reinstall official OS 1.2.1, then start again.'


def expected_entry(image):
    info = native_json.package_document(
        {
            'type': 'firmware',
            'name': 'OP-XY',
            'version': '1.2.1',
            'kind': 'official',
            'bytes': str(len(image)),
            'crc32': '%08x' % (zlib.crc32(image) & 0xFFFFFFFF),
            'sha256': hashlib.sha256(image).hexdigest(),
        }
    )
    files = (
        '%s  manifest.json\n%s  os.ldr\n' % (hashlib.sha256(info).hexdigest(), hashlib.sha256(image).hexdigest())
    ).encode()
    return {'os.ldr': image, 'manifest.json': info, 'files.sha256': files}


@unittest.skipIf(UNAVAILABLE, UNAVAILABLE)
class Adopt(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='xyf-adopt-')
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.work = Path(cls.tmp.name)
        cls.image = sim.STOCK_OS.read_bytes()
        cls.build_dir = sim.build()
        cls.runs = 0

    def setUp(self):
        type(self).runs += 1
        self.dir = self.work / ('run-%d' % self.runs)
        self.root = self.dir / 'yaffs2'
        self.dev = self.sim = None

    def tearDown(self):
        if self.dev:
            self.dev.t.close()
        if self.sim:
            self.sim.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    # -- helpers ---------------------------------------------------------------------------------
    def start(self, stock='stock', play=True, slow_ms=0):
        """slow_ms: A slowed from the first step on (the start's own job left to run while the test watches)."""
        sim.seed_fresh(self.root, stock, self.image)
        self.sim = sim.Simulator(
            self.root,
            self.dir / 'bootpart.bin',
            build_dir=self.build_dir,
            play=play,
            log=self.dir / 'log.txt',
            faults={'a_slow_ms': slow_ms} if slow_ms else None,
        ).start(settle=not slow_ms)
        if play:
            self.connect()

    def connect(self):
        if self.dev:
            self.dev.t.close()
        self.sim.wait_listening(bounds.WAIT_S)
        self.dev = host.Device(host.TcpPort(self.sim.address), on_log=None)
        self.assertEqual(self.dev.hello()[0], 0)

    def lines(self, marker):
        log = (self.dir / 'log.txt').read_text(errors='replace') if (self.dir / 'log.txt').exists() else ''
        return [line[line.find(marker) + len(marker) :] for line in log.splitlines() if marker in line]

    def wait(self, cond, what, timeout=bounds.WAIT_S):
        deadline = time.monotonic() + timeout
        while not cond():
            self.assertLess(time.monotonic(), deadline, what)
            self.sim.advance(20)

    def in_os(self):
        s = self.sim.status()
        return s['a'] == 'os' and s['b'] == 'os'

    def entry(self):
        folder = self.root / ENTRY
        return {p.name: p.read_bytes() for p in folder.iterdir()} if folder.is_dir() else {}

    def snapshot(self):
        return {
            p.relative_to(self.root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in self.root.rglob('*')
            if p.is_file() and not p.relative_to(self.root).as_posix().startswith('xy-forever/log')
        }

    def default(self):
        code, data = self.dev.read_file(STATE_FILE)
        self.assertEqual(code, 0)
        return dict(line.split('=', 1) for line in data.decode().splitlines() if '=' in line)['default']

    def settled(self):
        """The default once no job runs and storage answers (None before)."""
        code, f = self.job()
        if code == 0 and f['phase'] < 8:
            return None
        code, data = self.dev.read_file(STATE_FILE)
        return (
            dict(line.split('=', 1) for line in data.decode().splitlines() if '=' in line)['default']
            if code == 0
            else None
        )

    def adopted(self):
        self.assertEqual(self.entry(), expected_entry(self.image))  # byte-exact, nothing else
        tmp = self.root / 'xy-forever/tmp'
        self.assertEqual(sorted(p.name for p in tmp.iterdir()) if tmp.exists() else [], [])  # no leftovers
        self.assertEqual((self.root / sim.STOCK_UPGRADE).read_bytes(), self.image)  # the source stays
        self.assertEqual(self.sim.status()['rw_open'], 0)

    def quiet(self, still=1.5):
        """Storage at rest: A's write window closed and no mutating storage operation for `still` seconds (the
        converge sequence after an adoption writes its fingerprint; a cut fingerprint is not written again), in
        device time."""
        ops = None
        for _ in range(int(120 / still)):
            s = self.sim.status()
            if s['rw_open'] == 0 and s['storage_ops'] == ops:
                return
            ops = s['storage_ops'] if s['rw_open'] == 0 else None
            self.sim.advance(int(still * 1000))
        self.fail('storage never came to rest')

    def job(self):
        code, f = MS.job_status(self.dev, 0)
        return (code, f)

    def refused(self, stock, error, line):
        self.start(stock, play=False)
        self.wait(lambda: self.lines('menu: save failed: '), 'the refusal')
        self.assertEqual(self.lines('menu: save failed: '), [line])
        self.connect()  # the menu stays, with USB
        code, f = self.job()
        self.assertEqual(
            (code, MS.PHASES[f['phase']], f['error'], f['detail']), (0, 'failed', error, 'adopt opxy-121'), f
        )
        self.assertFalse((self.root / 'xy-forever/firmware').exists())
        self.assertFalse((self.root / 'xy-forever/tmp').exists())  # nothing written before the hash matched
        self.assertFalse((self.root / 'xy-forever/state-a.txt').exists())
        self.assertFalse(self.in_os())

    # -- the tests ---------------------------------------------------------------------------------
    @pytest.mark.sim
    def test_first_plain_start_adopts_converges_and_boots_it(self):
        """Kept: The first start after install adopts the stock OS and boots it."""
        self.start(play=False)
        self.wait(self.in_os, 'both in the OS')
        self.assertEqual(self.lines('menu: boots the default '), ['opxy-121'])
        self.assertEqual(self.lines('menu: save failed: '), [])
        self.assertIn('Adding OS 1.2.1', ' '.join(self.lines('progress: job=')))
        self.adopted()
        state = (self.root / 'xy-forever/state-a.txt').read_text()
        self.assertIn('default=opxy-121\n', state)
        self.assertIn('default=opxy-121\n', (self.root / 'xy-forever/converged.txt').read_text())

    @pytest.mark.sweep
    def test_power_cut_mid_copy_is_redone_at_the_next_start(self):
        self.start()
        self.wait(lambda: self.lines('menu: line: OS 1.2.1'), 'the first adoption')
        total = self.sim.status()['storage_ops']  # every mutating storage operation
        self.assertGreater(total, 6)
        for at in sorted({1, 2, 3, 5, total // 4, total // 2, 3 * total // 4, total - 1}):
            with self.subTest(cut_after=at):
                shutil.rmtree(self.root)  # a just-installed device again
                sim.seed_fresh(self.root, 'stock', self.image)
                cuts = self.sim.status()['cuts']
                self.sim.set('cut_after', at)  # the next start's at-th change: a cut
                self.sim.control('power play')
                self.wait(lambda: self.sim.status()['cuts'] > cuts, 'the cut')
                self.sim.set('cut_after', 0)
                self.connect()  # adopted again, or only the fingerprint
                self.wait(lambda: self.settled() == 'opxy-121', 'adopted after the cut')  # was cut (then nothing to do)
                self.quiet()  # the sequence ended, before the next rmtree
                self.adopted()
                self.assertEqual(self.lines('menu: save failed: '), [])


if __name__ == '__main__':
    unittest.main()
