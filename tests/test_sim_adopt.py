"""In the simulator: the first start after Track 5 wrote XY Forever onto a stock OP-XY (#178). The library is empty, so
the device adopts the stock updater's own os.ldr (/yaffs2/upgrade/firmware/blackfin/bin/os.ldr): it hashes it (the
exact stock 1.2.1 image, else it writes nothing), copies it into tmp/, reads it back, writes manifest.json and files.sha256
(as `xyf_host.py library-add` and a package publish), renames the folder in as library entry opxy-121, makes it the
default and converges (#197). A plain power-on then boots it; PLAY at power-on adopts and stays in the menu, and
job.status reports the adoption. A missing or wrong file refuses with a plain reason; a power cut mid-copy is redone at
the next start; a second start writes nothing. Needs clang++ and the private stock 1.2.1 images (skipped without
them). No device access."""
import hashlib
from pathlib import Path
import shutil
import sys
import tempfile
import time
import unittest
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
import xyf_host as host                                                   # noqa: E402
from xy_forever import bounds, modsend as MS, native_json, sim, usb_proto_ids as ids           # noqa: E402
from test_sim_mod_job import UNAVAILABLE                                  # noqa: E402

ENTRY = 'xy-forever/firmware/opxy-121'
STATE_FILE = '/yaffs2/xy-forever/state-a.txt'
REINSTALL = ' Reinstall official OS 1.2.1, then start again.'


def expected_entry(image):
    info = native_json.package_document({
        'type': 'firmware', 'name': 'OP-XY', 'version': '1.2.1', 'kind': 'official', 'bytes': str(len(image)),
        'crc32': '%08x' % (zlib.crc32(image) & 0xffffffff), 'sha256': hashlib.sha256(image).hexdigest(),
    })
    files = ('%s  manifest.json\n%s  os.ldr\n' % (hashlib.sha256(info).hexdigest(), hashlib.sha256(image).hexdigest())).encode()
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
    def start(self, stock='stock', play=True):
        sim.seed_fresh(self.root, stock, self.image)
        self.sim = sim.Simulator(self.root, self.dir / 'bootpart.bin', build_dir=self.build_dir, play=play,
                                 log=self.dir / 'log.txt').start()
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
        return [line[line.find(marker) + len(marker):] for line in log.splitlines() if marker in line]

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
        return {p.relative_to(self.root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in self.root.rglob('*') if p.is_file() and not p.relative_to(self.root).as_posix().startswith('xy-forever/log')}

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
        return dict(line.split('=', 1) for line in data.decode().splitlines() if '=' in line)['default'] if code == 0 else None

    def adopted(self):
        self.assertEqual(self.entry(), expected_entry(self.image))           # byte-exact, nothing else
        tmp = self.root / 'xy-forever/tmp'
        self.assertEqual(sorted(p.name for p in tmp.iterdir()) if tmp.exists() else [], [])   # no leftovers
        self.assertEqual((self.root / sim.STOCK_UPGRADE).read_bytes(), self.image)            # the source stays
        self.assertEqual(self.sim.status()['rw_open'], 0)

    def quiet(self, still=1.5):
        """Storage at rest: A's write window closed and no mutating storage operation for `still` seconds (the
        converge sequence after an adoption writes its fingerprint; a cut fingerprint is not written again)."""
        ops = None
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            s = self.sim.status()
            if s['rw_open'] == 0 and s['storage_ops'] == ops:
                return
            ops = s['storage_ops'] if s['rw_open'] == 0 else None
            time.sleep(still)
        self.fail('storage never came to rest')

    def job(self):
        code, f = MS.job_status(self.dev, 0)
        return (code, f)

    def refused(self, stock, error, line):
        self.start(stock, play=False)
        self.wait(lambda: self.lines('menu: save failed: '), 'the refusal')
        self.assertEqual(self.lines('menu: save failed: '), [line])
        self.connect()                                                       # the menu stays, with USB
        code, f = self.job()
        self.assertEqual((code, MS.PHASES[f['phase']], f['error'], f['detail']), (0, 'failed', error, 'adopt opxy-121'), f)
        self.assertFalse((self.root / 'xy-forever/firmware').exists())
        self.assertFalse((self.root / 'xy-forever/tmp').exists())          # nothing written before the hash matched
        self.assertFalse((self.root / 'xy-forever/state-a.txt').exists())
        self.assertFalse(self.in_os())

    # -- the tests ---------------------------------------------------------------------------------
    def test_first_plain_start_adopts_converges_and_boots_it(self):
        self.start(play=False)
        self.wait(self.in_os, 'both in the OS')
        self.assertEqual(self.lines('menu: boots the default '), ['opxy-121'])
        self.assertEqual(self.lines('menu: save failed: '), [])
        self.assertIn('Adding OS 1.2.1', ' '.join(self.lines('progress: job=')))
        self.adopted()
        state = (self.root / 'xy-forever/state-a.txt').read_text()
        self.assertIn('default=opxy-121\n', state)
        self.assertIn('default=opxy-121\n', (self.root / 'xy-forever/converged.txt').read_text())

    def test_a_second_start_writes_nothing(self):
        self.start(play=False)
        self.wait(self.in_os, 'both in the OS')
        before, ons = self.snapshot(), self.sim.status()['power_ons']
        self.sim.control('power plain')
        self.wait(lambda: self.sim.status()['power_ons'] > ons and self.in_os(), 'the second boot')
        self.assertEqual(self.lines('menu: boots the default '), ['opxy-121'])   # no menu: the default at once
        self.assertEqual(self.snapshot(), before)

    def test_play_start_adopts_and_stays_in_the_menu(self):
        self.start()
        self.sim.set('a_slow_ms', 2)                                         # long enough to watch it run
        seen = set()
        while True:
            code, f = self.job()
            if code == ids.S_NOT_FOUND:                                      # not started yet
                self.sim.advance(10)
                continue
            self.assertEqual(code, 0)
            if f['phase'] < 8:
                self.assertEqual(f['detail'], 'adopt opxy-121', f)
                seen.add(MS.PHASES[f['phase']])
                if f['phase'] in (1, 5, 6):
                    self.assertEqual(f['total'], len(self.image), f)
                if f['phase'] in (1, 5):
                    self.assertEqual(self.dev.read_file(STATE_FILE)[0], ids.S_BUSY)   # file commands wait for it
                self.sim.advance(10)
                continue
            break
        self.sim.set('a_slow_ms', 0)
        self.assertEqual((MS.PHASES[f['phase']], f['error'], f['detail']), ('done', 0, 'opxy-121'), f)
        self.assertTrue({'write', 'verify'} <= seen, seen)
        self.wait(lambda: self.lines('menu: line: OS 1.2.1 is in the library and is the default.'), 'the menu line')
        self.assertEqual(self.lines('menu: boots the default '), [])         # the menu stays
        self.wait(lambda: self.dev.read_file(STATE_FILE)[0] == 0, 'storage free again')
        self.assertEqual(self.default(), 'opxy-121')
        self.adopted()

    def test_missing_file_refuses(self):
        self.refused('missing', 4, 'No official OS 1.2.1 found.' + REINSTALL)

    def test_wrong_file_refuses(self):
        self.refused('wrong', 10, 'OS file is not official 1.2.1.' + REINSTALL)

    @bounds.slow                                    # #233: needs longer under the paced clock
    def test_power_cut_mid_copy_is_redone_at_the_next_start(self):
        self.start()
        self.wait(lambda: self.lines('menu: line: OS 1.2.1'), 'the first adoption')
        total = self.sim.status()['storage_ops']                             # every mutating storage operation
        self.assertGreater(total, 6)
        for at in sorted({1, 2, 3, 5, total // 4, total // 2, 3 * total // 4, total - 1}):
            with self.subTest(cut_after=at):
                shutil.rmtree(self.root)                                     # a just-installed device again
                sim.seed_fresh(self.root, 'stock', self.image)
                cuts = self.sim.status()['cuts']
                self.sim.set('cut_after', at)                                # the next start's at-th change: a cut
                self.sim.control('power play')
                self.wait(lambda: self.sim.status()['cuts'] > cuts, 'the cut')
                self.sim.set('cut_after', 0)
                self.connect()                                               # adopted again, or only the fingerprint
                self.wait(lambda: self.settled() == 'opxy-121', 'adopted after the cut')   # was cut (then nothing to do)
                self.quiet()                                                 # the sequence ended, before the next rmtree
                self.adopted()
                self.assertEqual(self.lines('menu: save failed: '), [])

    def test_play_start_with_a_record_and_no_default_writes_nothing(self):
        """check()'s own condition: a record and the stock entry, no default: a PLAY start converges to nothing."""
        self.start(play=False)
        self.wait(self.in_os, 'both in the OS')
        (self.root / 'xy-forever/config.txt').write_text('format=1\nbase=opxy-121\nmods=\n')
        (self.root / 'xy-forever/state-a.txt').unlink()                     # no default
        before, ons, lines = self.snapshot(), self.sim.status()['power_ons'], len(self.lines('menu: line: '))
        self.sim.control('power play')
        self.wait(lambda: self.sim.status()['power_ons'] > ons, 'the PLAY start')
        self.connect()
        self.wait(lambda: self.dev.read_file('/yaffs2/xy-forever/config.txt')[0] == 0, 'the menu serves storage')
        self.sim.advance(3000)                                                        # the record read, the menu settled
        self.assertEqual(self.job()[0], ids.S_NOT_FOUND)                    # no job this boot
        self.assertEqual(self.lines('menu: line: ')[lines:], [])
        self.assertEqual(self.lines('menu: save failed: '), [])
        self.assertEqual(self.snapshot(), before)

    def test_an_unreadable_library_is_never_adopted(self):
        """A's library scan fails (the catalog comes empty): the entry already there is never called incomplete."""
        self.start(play=False)
        self.wait(self.in_os, 'both in the OS')
        before, ons = self.snapshot(), self.sim.status()['power_ons']
        self.sim.set('fail_list', 1)
        self.sim.control('power plain')
        self.wait(lambda: self.sim.status()['power_ons'] > ons and self.lines('menu: save failed: '), 'the refusal')
        self.assertEqual(self.lines('menu: save failed: '), ['Failed: the library could not be read.'])
        self.connect()
        code, f = self.job()
        self.assertEqual((code, MS.PHASES[f['phase']], f['error'], f['code'], f['detail']),
                         (0, 'failed', 8, ids.S_STORAGE, 'adopt opxy-121'), f)
        self.sim.set('fail_list', 0)
        self.assertEqual(self.snapshot(), before)                            # nothing written, the entry kept
        self.assertFalse(self.in_os())

if __name__ == '__main__':
    unittest.main()
