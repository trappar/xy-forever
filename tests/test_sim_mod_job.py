"""The mod assembly job on processor B in the simulator (mods.apply, job.status, job.cancel;
the commit into library entry mods-a / mods-b, "content not installed", the tmp/job-* sweep).

B runs native/mod_job from its menu loop over its timed file client; the entry it leaves in
/yaffs2/xy-forever/tmp/job-<id>/ must equal test_mod_job.py's reference (the one-shot host assembly,
completed by modpatch.entry_files) byte for byte, and nothing outside that folder may change. Faults
(cancel, a failed storage write, A going silent) end with no os.ldr, the write window closed and B
answering; a later job works. Synthetic packages always; the published 0.0.6 set when present. Needs
clang++ and the private stock 1.2.1 images (skipped without them). No device access."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import struct
import shutil
import sys
import tempfile
import threading
import time
import unittest
import warnings
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
import xyf_host as host                                                   # noqa: E402
from xy_forever import xyf_entry, census, hostbuild, modkit as M, modpatch as MP, modsend as MS, paths, sim   # noqa: E402
from xy_forever import usb_proto as P, usb_proto_ids as ids                # noqa: E402
from test_modpatch import CATALOG, OFFICIAL, PACKAGES, finish, kit_for, official_text, profile_for, stock  # noqa: E402
import test_modkit as TK                                                  # noqa: E402

UNAVAILABLE = sim.available() or (None if sim.STOCK_OS.is_file() else 'private stock 1.2.1 os.ldr absent')
NAME = 'OP-XY mods'
TMP = 'xy-forever/tmp'
LIB = 'xy-forever/firmware'
FILE_COMMAND = (P.READ_FILE, b'/yaffs2/xy-forever/state-a.txt\0')


def tree(folder):
    folder = Path(folder)
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob('*') if p.is_file()} \
        if folder.exists() else {}


def outside_tmp(root):
    """Every file and folder of the storage but tmp/ and the reports (what the job must never touch)."""
    root = Path(root)
    keep = {}
    for p in sorted(root.rglob('*')):
        rel = p.relative_to(root).as_posix()
        if rel.startswith((TMP, 'xy-forever/log', LIB + '/mods-')):
            continue
        keep[rel] = hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else 'folder'
    return keep


class Base:
    """One simulator per test over a copy of the class's seed (packages, base entry, catalog)."""
    base = profile = generation = gen_dir = None
    unverified = False

    @classmethod
    def seed_with(cls, gen_dir, profile, stock_bytes, catalog, official_text_):
        cls.seeded = sim.seed(cls.work / 'seed')
        shutil.copytree(gen_dir, cls.seeded / 'xy-forever/mods' / profile / gen_dir.name)
        entry = cls.seeded / 'xy-forever/firmware' / cls.base
        entry.mkdir(parents=True, exist_ok=True)
        (entry / 'os.ldr').write_bytes(stock_bytes)
        (entry / 'info.txt').write_bytes(sim.info_text(stock_bytes, 'Base', '1.2.1'))
        (entry / 'content.sha256').write_text(official_text_)
        (cls.seeded / 'content/op1_factory.json').write_bytes(catalog)
        cls.profile, cls.generation, cls.gen_dir = profile, gen_dir.name, gen_dir
        cls.binary = MP.build(cls.work / 'bin', run=hostbuild.run)
        cls.runs = 0

    def setUp(self):
        type(self).runs += 1
        self.dir = self.work / ('run-%d' % self.runs)
        self.root = self.dir / 'yaffs2'
        shutil.copytree(self.seeded, self.root)
        if getattr(self, 'settings_text', None):                         # settings.txt for this test
            (self.root / 'xy-forever/settings.txt').write_bytes(self.settings_text)
        self.sim = sim.Simulator(self.root, self.dir / 'bootpart.bin', log=self.dir / 'log.txt').start()
        self.dev = host.Device(host.TcpPort(self.sim.address), on_log=None)
        self.assertEqual(self.dev.hello()[0], 0)

    def tearDown(self):
        self.dev.t.close()
        self.sim.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    # -- helpers ---------------------------------------------------------------------------------
    def start(self, machines, exts=(), **kw):
        code, job, missing = MS.start(self.dev, kw.pop('base', self.base), self.profile, self.generation, machines,
                                      exts, NAME, self.unverified)
        self.assertEqual((code, missing), (0, 0))
        self.assertGreater(job, 0)
        return job

    def status(self, job=0):
        code, f = MS.job_status(self.dev, job)
        self.assertEqual(code, 0)
        return f

    def finished(self, job, timeout=120.0, served=True):
        deadline = time.monotonic() + timeout
        while True:
            f = self.status(job)
            if f['phase'] in (MS.DONE, MS.FAILED, MS.CANCELLED):
                if served:                                                  # the window closed (as --wait)
                    self.assertIn(MS.served(self.dev, out=lambda line: None), (0, ids.S_NOT_FOUND))
                return f
            self.assertLess(time.monotonic(), deadline, 'job %d did not end: %s' % (job, f))
            time.sleep(0.02)

    def until_phase(self, job, phase, timeout=60.0):
        deadline = time.monotonic() + timeout
        while True:
            f = self.status(job)
            if f['phase'] >= phase:
                return f
            self.assertLess(time.monotonic(), deadline, 'job %d never reached phase %d: %s' % (job, phase, f))
            time.sleep(0.005)

    def output(self, job):
        return self.root / TMP / ('job-%d' % job)

    def entry(self, name):
        return self.root / LIB / name

    def catalog(self):
        """B's menu catalog (as the device pass reads it: PEEK of its buffer)."""
        data = b''
        while len(data) < 2048:
            code, body = self.dev.call(P.PEEK, struct.pack('<II', census.CATALOG_ADDRESS + len(data), 1024))
            self.assertEqual((code, len(body)), (0, 1028))
            data += body[4:]                                                # after the address word
        found = census.catalog(data)
        self.assertIsNotNone(found)
        return found

    def listed(self, name):
        """The catalog item of library entry name, or None."""
        items = [i for i in self.catalog()['items'] if i['id'] == name]
        return items[0] if items else None

    def leftovers(self):
        tmp = self.root / TMP
        return sorted(p.name for p in tmp.iterdir()) if tmp.exists() else []

    def reference(self, machines, exts):
        folder = Path(tempfile.mkdtemp(dir=self.dir))
        job = folder / 'job.txt'
        job.write_text(MP.job_text(self.gen_dir, machines, exts, self.root / 'xy-forever/firmware' / self.base / 'os.ldr',
                                   self.root / 'content/op1_factory.json',
                                   official=self.root / 'xy-forever/firmware' / self.base / 'content.sha256',
                                   unverified=self.unverified, name=NAME))
        report = MP.run(self.binary, job, folder / 'ref')
        self.assertEqual([report[k] for k in ('input', 'patch', 'verify', 'content', 'copy')], ['ok'] * 5, report)
        return MP.entry_files(self.gen_dir, MP.tree(folder / 'ref'), machines, name=NAME)

    def assemble_and_compare(self, machines, exts=()):
        before = outside_tmp(self.root)
        t = time.monotonic()
        job = self.start(machines, exts)
        f = self.finished(job)
        seconds = time.monotonic() - t
        self.assertEqual((MS.PHASES[f['phase']], MS.ERRORS[f['error']], f['debris']), ('done', 'none', 0), f)
        self.assertIn(f['detail'], ('mods-a', 'mods-b'), f)                 # the library entry
        self.assertFalse(self.output(job).exists())                         # moved into the library
        got, want = tree(self.entry(f['detail'])), self.reference(machines, exts)
        self.assertEqual(sorted(got), sorted(want))                         # no .part, nothing else
        for path in want:
            self.assertEqual(got[path], want[path], path)
        self.assertEqual(outside_tmp(self.root), before)                    # nothing but tmp/ and the mods ids
        item = self.listed(f['detail'])                                     # listed at once (A rescanned)
        self.assertEqual((item['name'], item['usable'], item['content_missing']), (NAME, False, True), item)
        self.assertEqual(self.sim.status()['rw_open'], 0)                   # the write window closed
        print('\n%s: %d machine(s) + %d extension(s): %d steps, longest pass %d ms, %d ms device clock, %.2f s host, '
              'peak %d B' % (type(self).__name__, len(machines), len(exts), f['steps'], f['longest_ms'], f['elapsed_ms'],
                             seconds, f['peak']), file=sys.stderr)
        return job, f

    def assert_clean_failure(self, job, f, phase, error):
        self.assertEqual((MS.PHASES[f['phase']], MS.ERRORS[f['error']]), (phase, error), f)
        self.assertIn(MS.served(self.dev, out=lambda line: None), (0, ids.S_NOT_FOUND))   # window closed
        left = tree(self.output(job))
        self.assertNotIn('os.ldr', left)
        self.assertFalse([p for p in left if p.endswith('.part')], left)
        self.assertEqual((f['debris'], self.output(job).exists()), (0, False))   # files and folders removed
        self.assertEqual(self.sim.status()['rw_open'], 0)
        self.assertEqual(self.dev.read_file('/yaffs2/xy-forever/state-a.txt')[0], 0)   # B and A answer


@unittest.skipIf(UNAVAILABLE, UNAVAILABLE)
class SyntheticJob(Base, unittest.TestCase):
    """modkit packages split from test_modpatch.py's synthetic kit (an unverified base, confirmed)."""
    base = 'modbase'
    unverified = True

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='xyf-simjob-')
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.work = Path(cls.tmp.name)
        data = stock()
        (cls.work / 'kit.json').write_text(json.dumps(kit_for(profile_for(data))))
        gen = Path(M.split_kit(cls.work / 'kit.json', cls.work / 'packages', finish=finish,
                               sdk_source='https://github.com/example/sdk@abc')['root'])
        cls.seed_with(gen, MP.info_values(gen / 'base/info.txt')['profile'], data, CATALOG, official_text(OFFICIAL))

    def test_entries_match_the_reference(self):
        """The first build becomes mods-a, the second mods-b (absent), the third replaces mods-a (both
        there, neither the default: the one not written last); the other entry, the default and tmp/ stay."""
        seen = []
        for machines, exts, want in ((['alpha'], [], 'mods-a'), (['alpha', 'beta', 'gamma', 'delta'], [], 'mods-b'),
                                     (['alpha', 'gamma'], ['cap', 'flt'], 'mods-a')):
            with self.subTest(machines=machines, extensions=exts):
                other = tree(self.entry('mods-b' if want == 'mods-a' else 'mods-a'))
                job, f = self.assemble_and_compare(machines, exts)
                self.assertEqual(f['detail'], want)
                self.assertNotIn('layout.json', tree(self.entry(want)))
                self.assertEqual(xyf_entry.parse_manifest(tree(self.entry(want))['info.txt'])['machines'], len(machines))
                self.assertEqual(tree(self.entry('mods-b' if want == 'mods-a' else 'mods-a')), other)
                self.assertEqual(self.leftovers(), [])                      # the old mods-a removed from tmp/
                seen.append(f['detail'])
        self.assertEqual(self.catalog()['items'][self.catalog()['default']]['id'], 'opxy-121')

    def test_progress_busy_and_one_job_at_a_time(self):
        """While the job runs (A slowed): job.status moves; every command that needs A or the staging is busy,
        a second mods.apply too; the screen, diag.status and job.status answer. A staged upload in progress
        refuses a start (busy)."""
        self.assertEqual(self.dev.call(P.PUT_BEGIN, (100).to_bytes(4, 'little') + bytes(36))[0], 0)
        code, job, _ = MS.start(self.dev, self.base, self.profile, self.generation, ['alpha'], (), NAME, True)
        self.assertEqual((code, job), (ids.S_BUSY, 0))                      # an upload is in progress
        self.assertEqual(self.dev.stage(b'complete'), 0)
        self.sim.set('a_slow_ms', 4)
        job = self.start(['alpha', 'beta'])
        seen = []
        f = self.until_phase(job, 1)
        while f['phase'] not in (MS.DONE, MS.FAILED):
            seen.append((f['phase'], f['steps']))
            if len(seen) == 3:
                self.assertEqual(self.dev.call(*FILE_COMMAND)[0], ids.S_BUSY)
                self.assertEqual(self.dev.call(P.PUT_BEGIN, (4).to_bytes(4, 'little') + bytes(36))[0], ids.S_BUSY)
                self.assertEqual(MS.start(self.dev, self.base, self.profile, self.generation, ['alpha'], (), NAME,
                                          True)[0], ids.S_BUSY)
                self.assertEqual(self.dev.call(P.STATUS)[0], 0)
                self.assertEqual(self.dev.call(P.SCREEN, (0).to_bytes(4, 'little'))[0], 0)
                self.assertEqual(MS.job_cancel(self.dev, job + 7)[0], ids.S_NOT_FOUND)
            f = self.status(job)
        self.sim.set('a_slow_ms', 0)
        self.assertEqual(MS.PHASES[f['phase']], 'done', f)
        self.assertGreater(len({p for p, _ in seen}), 2, seen)             # several phases seen
        self.assertEqual([s for _, s in seen], sorted(s for _, s in seen))
        self.assertGreater(seen[-1][1], seen[0][1])
        self.assertEqual(MS.served(self.dev, out=lambda line: None), 0)     # busy until the window closed
        self.assertEqual(self.dev.call(*FILE_COMMAND)[0], 0)              # served again
        self.assertEqual(MS.job_cancel(self.dev, job), (ids.S_STATE, 0))     # it ended
        self.assertEqual(self.status(0)['job'], job)
        self.assertEqual(MS.job_status(self.dev, job + 1)[0], ids.S_NOT_FOUND)

    def test_cancel_removes_everything(self):
        self.sim.set('a_slow_ms', 4)
        job = self.start(['alpha', 'gamma'], ['cap', 'flt'])
        self.until_phase(job, 6)                                            # verify: outputs written
        self.assertTrue(self.output(job).exists())
        self.assertEqual(MS.job_cancel(self.dev, job), (0, 0))
        self.sim.set('a_slow_ms', 0)
        self.assert_clean_failure(job, self.status(job), 'cancelled', 'cancelled')
        self.assemble_and_compare(['alpha'])                                # the next job works

    def test_cancel_with_a_large_output_answers_throughout(self):
        """a cancelled job whose output holds a large tree: the removal runs one step per
        loop pass ("cleaning up", storage commands busy); job.cancel, HELLO and status answer throughout (no
        answer takes a quarter of the removal), and the output is gone at the end."""
        self.sim.set('a_slow_ms', 4)
        job = self.start(['alpha', 'gamma'], ['cap', 'flt'])
        self.until_phase(job, 2)
        extra = self.output(job) / 'extra' / 'deep'
        extra.mkdir(parents=True, exist_ok=True)
        for i in range(60):                                                 # dozens of loop passes (~0.3 s each)
            (extra / ('%03d' % i)).write_bytes(b'x')
        begun = t = time.monotonic()
        self.assertEqual(MS.job_cancel(self.dev, job)[0], 0)
        slowest = time.monotonic() - t
        busy = 0
        while True:
            t = time.monotonic()
            self.assertEqual(self.dev.call(P.STATUS)[0], 0)
            self.assertEqual(self.dev.hello()[0], 0)
            code = self.dev.call(*FILE_COMMAND)[0]
            slowest = max(slowest, time.monotonic() - t)
            if code != ids.S_BUSY:
                break
            busy += 1
        total = time.monotonic() - begun                                    # (the machine's load sets the scale)
        self.assertGreater(busy, 3)                                         # B answered while it cleaned up
        self.assertLess(slowest, total / 4, (slowest, total, busy))
        self.sim.set('a_slow_ms', 0)
        self.assert_clean_failure(job, self.status(job), 'cancelled', 'cancelled')

    def test_storage_fault_then_a_second_job(self):
        self.sim.set('fail_write', 3)                                       # the third 1 KiB write fails (EIO)
        job = self.start(['alpha', 'gamma'])
        f = self.finished(job)
        self.assert_clean_failure(job, f, 'failed', 'write')
        self.assertTrue(f['detail'], f)
        job2, _ = self.assemble_and_compare(['alpha', 'gamma'])
        self.assertGreater(job2, job)

    def test_missing_inputs_and_refusals(self):
        before = outside_tmp(self.root)
        call = lambda **kw: MS.start(self.dev, kw.get('base', self.base), kw.get('profile', self.profile),   # noqa: E731
                                     kw.get('generation', self.generation), kw.get('machines', ['alpha']), (), NAME, True)
        (self.root / 'xy-forever/firmware' / self.base / 'content.sha256').rename(self.dir / 'content.sha256')
        self.assertEqual(call(), (ids.S_NOT_FOUND, 0, 2))
        shutil.move(str(self.dir / 'content.sha256'), str(self.root / 'xy-forever/firmware' / self.base))
        self.assertEqual(call(base='nosuch'), (ids.S_NOT_FOUND, 0, 1))
        self.assertEqual(call(generation='0.0.0-none'), (ids.S_NOT_FOUND, 0, 4))
        catalog = self.root / 'content/op1_factory.json'
        catalog.rename(self.dir / 'catalog')
        self.assertEqual(call(), (ids.S_NOT_FOUND, 0, 3))
        shutil.move(str(self.dir / 'catalog'), str(catalog))
        self.assertEqual(call(generation='../x')[0], ids.S_BAD_REQUEST)
        self.assertEqual(call(machines=['alpha', '../x'])[0], ids.S_BAD_REQUEST)
        self.assertEqual(outside_tmp(self.root), before)
        self.assertFalse((self.root / TMP).exists())
        # A package that is not there: the job starts, fails (open), leaves nothing.
        job = self.start(['alpha', 'nosuch'])
        self.assert_clean_failure(job, self.finished(job), 'failed', 'open')

    def test_up_to_64_machines_parsed(self):
        """The request parser takes 40 and 64 machines (64 ids filling the field's 1023 bytes); a 65th is
        bad_request. Only the packages present decide what follows (here: the first absent one fails the job at
        open, before anything is built); memory is the job's limit, not the count."""
        call = lambda machines: MS.start(self.dev, self.base, self.profile, self.generation, machines, (), NAME, True)   # noqa: E731
        sixty_four = ['alpha'] + ['m%014d' % i for i in range(63)]
        self.assertEqual(len(','.join(sixty_four)), 1013)
        self.assertEqual(call(['alpha'] + ['m%014d' % i for i in range(64)])[0], ids.S_BAD_REQUEST)   # 65
        self.assertEqual(call(sixty_four[:-1] + ['Bad'])[0], ids.S_BAD_REQUEST)                       # a bad id
        self.assertFalse((self.root / TMP).exists())
        for machines in (sixty_four[:40], sixty_four):
            with self.subTest(count=len(machines)):
                job = self.start(machines)
                self.assert_clean_failure(job, self.finished(job), 'failed', 'open')

    def test_a_silent_fails_with_link_and_b_answers(self):
        """A stops answering mid-write: the request runs out its bound, the job fails with link at once (no more
        requests: debris 1, the parts stay for the tmp/ sweep), never an os.ldr; B keeps answering USB."""
        self.sim.set('a_slow_ms', 4)
        job = self.start(['alpha', 'gamma'], ['cap', 'flt'])
        self.until_phase(job, 5)                                            # write
        before = self.sim.status()
        self.sim.set('a_silent', 1)
        deadline = time.monotonic() + 30.0
        while self.sim.status()['a_silenced'] == before['a_silenced']:     # B's next request is held
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.005)
        reads = self.sim.status()['b_clock_reads']
        while self.sim.status()['b_clock_reads'] - reads < 100:             # B waits on it
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.005)
        reply = {}
        thread = threading.Thread(target=lambda: reply.setdefault('r', MS.job_status(self.dev, job)), daemon=True)
        thread.start()                                                      # answered once the job ended
        for _ in range(8):                                                  # the request's bound, then the close's
            self.sim.advance(6000, step=None)
            self.sim.ran_on()                                               # B saw the jump
            thread.join(0.1)
            if 'r' in reply:
                break
        thread.join(10.0)
        code, f = reply['r']
        self.assertEqual(code, 0)
        self.assertEqual((MS.PHASES[f['phase']], MS.ERRORS[f['error']], f['debris']), ('failed', 'link', 1), f)
        self.assertNotIn('os.ldr', tree(self.output(job)))
        self.assertEqual(self.dev.call(P.STATUS, timeout=5.0)[0], 0)
        self.assertEqual(self.dev.call(*FILE_COMMAND, timeout=10.0)[0], ids.S_LINK)   # the link is down, B answers
        self.sim.set('a_silent', 0)

    def reconnect(self):
        self.dev.t.close()
        self.dev = host.Device(host.TcpPort(self.sim.address), on_log=None)

    def test_slow_window_close_b_stays_live(self):
        """The window close at the job's end takes seconds on the device (yaffs checkpoint). B never
        waits in it: every job.status, a new session's HELLO and diag.status right after done answer at once;
        file commands and mods.apply answer busy until A closed the window, then work (the build 172 freeze
        lead: B answered nothing, not even HELLO, until op 9 returned)."""
        self.sim.set('unmount_ms', 5000)
        job = self.start(['alpha', 'beta'])
        while True:
            t = time.monotonic()
            f = self.status(job)
            self.assertLess(time.monotonic() - t, 2.5, 'job.status waited on the window close')
            if f['phase'] in (MS.DONE, MS.FAILED):
                break
            time.sleep(0.005)
        self.assertEqual(MS.PHASES[f['phase']], 'done', f)
        done = time.monotonic()
        self.reconnect()                                                    # the CLI's session ended
        self.assertEqual(self.dev.call(P.HELLO, timeout=2.5)[0], 0)
        self.assertEqual(self.dev.call(P.STATUS, timeout=2.5)[0], 0)
        self.assertEqual(self.dev.call(*FILE_COMMAND, timeout=2.5)[0], ids.S_BUSY)
        self.assertEqual(MS.start(self.dev, self.base, self.profile, self.generation, ['alpha'], (), NAME, True)[0],
                         ids.S_BUSY)
        self.assertEqual(self.sim.status()['rw_open'], 1)                   # still closing
        while self.dev.call(*FILE_COMMAND, timeout=2.5)[0] == ids.S_BUSY:
            self.assertLess(time.monotonic() - done, 15.0)
            time.sleep(0.05)
        self.assertGreater(time.monotonic() - done, 3.0)                     # served once the close ended
        self.assertEqual(self.dev.call(*FILE_COMMAND, timeout=1.0)[0], 0)
        self.assertEqual(self.sim.status()['rw_open'], 0)
        self.assertTrue((self.entry(f['detail']) / 'os.ldr').is_file())
        self.sim.set('unmount_ms', 0)

    def test_window_close_past_its_bound_link_and_b_answers(self):
        """A never answers the close within its bound (change_ms): B stays live throughout, file
        commands answer busy, then link once the bound passed; when A answers late, they work again."""
        self.sim.set('unmount_ms', 60000)
        job = self.start(['alpha'])
        self.assertEqual(MS.PHASES[self.finished(job, timeout=30.0, served=False)['phase']], 'done')
        self.assertEqual(self.dev.call(*FILE_COMMAND, timeout=1.0)[0], ids.S_BUSY)
        self.sim.advance(46000, step=None)                                  # past change_ms (45 s)
        deadline = time.monotonic() + 10.0
        while self.dev.call(*FILE_COMMAND, timeout=2.0)[0] == ids.S_BUSY:
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.05)
        self.assertEqual(self.dev.call(*FILE_COMMAND, timeout=2.0)[0], ids.S_LINK)   # the request is still out
        self.assertEqual(self.dev.call(P.HELLO, timeout=1.0)[0], 0)
        self.assertEqual(self.dev.call(P.STATUS, timeout=1.0)[0], 0)
        self.sim.set('unmount_ms', 0)                                       # A answers late
        deadline = time.monotonic() + 10.0
        while self.dev.call(*FILE_COMMAND, timeout=6.0)[0] != 0:
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.1)

    # -- the commit into the library, "content not installed", the sweep ---------------------
    def slot(self, default='opxy-121', once=''):
        """A newer state slot (state-b.txt, seq 2) naming the default and the boot-once entry."""
        body = ('default=%s\n%sseq=2\n' % (default, 'once=%s\n' % once if once else '')).encode()
        (self.root / 'xy-forever/state-b.txt').write_bytes(body + b'crc32=%08x\n' % (zlib.crc32(body) & 0xffffffff))

    def two_entries(self, machines):
        """mods-a built (written last), mods-b a marked copy of it: the next build replaces mods-b. Returns the
        number of files of the entry (the job's own renames before the commit's)."""
        self.assemble_and_compare(machines)
        shutil.copytree(self.entry('mods-a'), self.entry('mods-b'))
        (self.entry('mods-b') / 'old.txt').write_bytes(b'the old entry\n')
        return len(tree(self.entry('mods-a')))

    def fault(self, n, kind, machines):
        self.sim.set('rename_fault_kind', kind)
        self.sim.set('rename_fault', n)
        return self.start(machines)

    def wait_sim(self, test, what, timeout=30.0):
        deadline = time.monotonic() + timeout
        while True:
            status = self.sim.status()
            if test(status):
                return status
            self.assertLess(time.monotonic(), deadline, what)
            time.sleep(0.05)

    def test_default_and_once_mods_entries_are_kept(self):
        """A mods entry that is the default (or the boot-once entry) is never replaced; with both
        ids taken mods.apply is refused (state) and writes nothing."""
        self.assemble_and_compare(['alpha'])                                # mods-a
        self.slot(default='mods-a')
        kept = tree(self.entry('mods-a'))
        for machines in (['alpha', 'beta'], ['alpha', 'gamma']):            # mods-b (absent), then mods-b again
            _, f = self.assemble_and_compare(machines)
            self.assertEqual(f['detail'], 'mods-b')
            self.assertEqual(tree(self.entry('mods-a')), kept)
            self.assertEqual(self.leftovers(), [])
        self.slot(default='mods-a', once='mods-b')
        before = outside_tmp(self.root), tree(self.entry('mods-b'))
        code, job, _ = MS.start(self.dev, self.base, self.profile, self.generation, ['alpha'], (), NAME, True)
        self.assertEqual((code, job), (ids.S_STATE, 0))
        self.assertEqual((outside_tmp(self.root), tree(self.entry('mods-b'))), before)
        self.assertEqual(tree(self.entry('mods-a')), kept)

    def press(self, *keys):
        for key, pressed in keys:
            self.assertEqual(self.dev.call(P.KEY, struct.pack('<II', key, pressed))[0], 0)

    def test_menu_actions_wait_for_the_job(self):
        """Continue, Boot once and Set default pressed while a build runs show the busy line
        and do nothing; the build completes."""
        play, plus, shift = 51, 54, 55
        self.sim.set('a_slow_ms', 4)
        job = self.start(['alpha', 'beta'])
        self.until_phase(job, 2)
        self.press((play, 1), (play, 0))                                    # Continue (the default's row)
        self.press((plus, 1), (plus, 0), (play, 1), (play, 0))              # the library
        self.press((play, 1), (play, 0))                                    # Boot once (back on the home rows)
        self.press((play, 1), (play, 0))                                    # the library again
        self.press((shift, 1), (play, 1), (play, 0), (shift, 0))            # Set default
        log = (self.dir / 'log.txt').read_text()
        self.assertEqual(log.count('menu: line: Busy: a build or content change is running.'), 3, log[-2000:])
        self.sim.set('a_slow_ms', 0)
        f = self.finished(job)
        self.assertEqual(MS.PHASES[f['phase']], 'done', f)
        self.assertNotIn('menu: continue', (self.dir / 'log.txt').read_text())

    def test_default_set_before_the_commit_keeps_the_entry(self):
        """the target (mods-b) becomes the default while the build runs: the commit
        re-reads the state slots and fails cleanly; mods-b, the default, stays as it was."""
        self.two_entries(['alpha'])
        old = tree(self.entry('mods-b'))
        self.sim.set('a_slow_ms', 4)
        job = self.start(['alpha', 'gamma'])
        self.until_phase(job, 2)
        self.slot(default='mods-b')
        self.sim.set('a_slow_ms', 0)
        f = self.finished(job)
        self.assertEqual((MS.PHASES[f['phase']], MS.ERRORS[f['error']], f['detail']), ('failed', 'rename', 'commit taken'), f)
        self.assertEqual(tree(self.entry('mods-b')), old)
        self.assertEqual(self.leftovers(), [])
        self.assertEqual(self.sim.status()['rw_open'], 0)

    def test_content_not_installed_is_refused(self):
        """The new entry is listed (its name, content not installed) but library.boot refuses it with no_content
        (set default installs its content first: test_sim_content_switch.py); the state slots and the
        default stay, B stays home."""
        self.assemble_and_compare(['alpha'])
        slots = tree(self.root / 'xy-forever')
        slots = {k: v for k, v in slots.items() if k.startswith('state')}
        self.assertEqual(self.dev.library_boot('mods-a'), ids.S_NO_CONTENT)
        self.assertEqual(self.dev.library_boot('opxy-121-stock'.replace('-stock', '-none')), ids.S_NOT_FOUND)
        self.assertEqual({k: v for k, v in tree(self.root / 'xy-forever').items() if k.startswith('state')}, slots)
        self.assertEqual(self.dev.call(P.STATUS)[0], 0)                      # still home: nothing booted
        found = self.catalog()
        self.assertEqual(found['items'][found['default']]['id'], 'opxy-121')

    def test_commit_faults_leave_the_old_entry_or_none(self):
        """A failed rename at each commit step: away (the old entry stays), in (B renames the old entry back);
        without an old entry a failed rename in leaves none listed. tmp/ is clean, the window closed."""
        files = self.two_entries(['alpha'])
        old, other = tree(self.entry('mods-b')), tree(self.entry('mods-a'))
        for n, step in ((files + 1, 'away'), (files + 2, 'in')):
            with self.subTest(step=step):
                f = self.finished(self.fault(n, 1, ['alpha']))
                self.assertEqual((MS.PHASES[f['phase']], MS.ERRORS[f['error']], f['detail'], f['debris']),
                                 ('failed', 'rename', 'commit ' + step, 0), f)
                self.assertEqual(tree(self.entry('mods-b')), old)
                self.assertEqual(tree(self.entry('mods-a')), other)
                self.assertEqual(self.leftovers(), [])
                self.assertEqual(self.sim.status()['rw_open'], 0)
                self.assertTrue(self.listed('mods-b'))
                self.assertEqual(self.dev.read_file('/yaffs2/xy-forever/state-a.txt')[0], 0)
        shutil.rmtree(self.entry('mods-a'))
        shutil.rmtree(self.entry('mods-b'))
        f = self.finished(self.fault(files + 1, 1, ['alpha']))             # no old entry: the rename in fails
        self.assertEqual((MS.ERRORS[f['error']], f['detail']), ('rename', 'commit in'), f)
        self.assertEqual((self.entry('mods-a').exists(), self.listed('mods-a'), self.leftovers()), (False, None, []))

    def test_power_cut_at_each_commit_step_then_the_sweep(self):
        """A power cut right after the rename in (the new entry listed) and right after the rename away (the
        target missing, the other entry intact); the next job first sweeps tmp/job-* (the old tree, the
        unfinished output), never anything else."""
        files = self.two_entries(['alpha'])
        (self.root / TMP / 'keep').mkdir()
        cuts = self.sim.status()['cuts']
        for n, target, other in ((files + 2, 'mods-b', 'mods-a'), (files + 1, 'mods-a', 'mods-b')):
            with self.subTest(n=n - files):                                 # after a cut no last write is known: mods-a
                kept = tree(self.entry(other))
                self.fault(n, 2, ['alpha'])
                cuts += 1
                self.wait_sim(lambda s: s['cuts'] == cuts and s['listening'], 'power cut and restart')
                self.reconnect()
                self.assertEqual(self.dev.hello()[0], 0)
                self.assertEqual(len([n for n in self.leftovers() if n.startswith('job-')]), 2 if n == files + 1 else 1)
                self.assertEqual(tree(self.entry(other)), kept)
                if n == files + 2:                                          # the new entry, without the old marker
                    self.assertEqual(sorted(tree(self.entry(target))), sorted(tree(self.entry(other))))
                    self.assertTrue(self.listed(target))
                else:                                                       # the target missing, not listed
                    self.assertFalse(self.entry(target).exists())
                    self.assertIsNone(self.listed(target))
                self.assertEqual(self.dev.read_file('/yaffs2/xy-forever/state-a.txt')[0], 0)
        self.assemble_and_compare(['alpha'])                                # the next build works, sweeps first
        self.assertEqual(self.leftovers(), ['keep'])

    def a_silent_case(self, step):
        files = self.two_entries(['alpha', 'gamma'])
        job = self.fault(files + (1 if step == 'away' else 2), 3, ['alpha', 'gamma'])
        self.wait_sim(lambda s: s['a_silent'] == 1, 'A silent at the rename')
        reply = {}
        thread = threading.Thread(target=lambda: reply.setdefault('r', MS.job_status(self.dev, job)), daemon=True)
        thread.start()
        for _ in range(12):                                                 # the rename's bound (45 s), then the close's
            self.sim.advance(6000, step=None)
            self.sim.ran_on()                                               # B saw the jump
            thread.join(0.1)
            if 'r' in reply:
                break
        thread.join(10.0)
        code, f = reply['r']
        self.assertEqual(code, 0)
        self.assertEqual((MS.PHASES[f['phase']], MS.ERRORS[f['error']], f['detail'], f['debris']),
                         ('failed', 'link', 'commit ' + step, 1), f)
        self.assertEqual(self.dev.call(P.STATUS, timeout=5.0)[0], 0)        # B answers
        self.assertEqual(self.dev.call(*FILE_COMMAND, timeout=10.0)[0], ids.S_LINK)
        self.assertTrue((self.entry('mods-a') / 'os.ldr').is_file())        # the other entry intact
        self.sim.set('a_silent', 0)
        return f

    def test_a_silent_at_the_rename_away(self):
        """A goes silent as it renames the old entry away: link, the target missing (neither listed: the old
        tree and the output wait in tmp/ for the sweep), B answers."""
        self.a_silent_case('away')
        self.assertFalse(self.entry('mods-b').exists())
        self.assertEqual(len([n for n in self.leftovers() if n.startswith('job-')]), 2)

    def test_a_silent_at_the_rename_in(self):
        """A goes silent as it renames the new entry in: link, the new entry is in place (the old tree waits in
        tmp/ for the sweep), B answers."""
        self.a_silent_case('in')
        self.assertTrue((self.entry('mods-b') / 'os.ldr').is_file())
        self.assertNotIn('old.txt', tree(self.entry('mods-b')))
        self.assertEqual([n for n in self.leftovers() if n.startswith('job-')], ['job-2-old'])

    def test_sweep_at_job_start_bounded(self):
        """Before its first step the job removes leftover tmp/job-* folders (files, subfolders), nothing else in
        tmp/; a leftover larger than the sweep's cap is cut short and the job still completes."""
        tmp = self.root / TMP
        for name, count in (('job-91', 3), ('job-92-old', 2)):
            sub = tmp / name / 'content' / 'display'
            sub.mkdir(parents=True)
            for i in range(count):
                (sub / ('%d.svg' % i)).write_bytes(b'x')
                (tmp / name / ('f%d' % i)).write_bytes(b'y')
        (tmp / 'keep').mkdir()
        (tmp / 'keep' / 'a').write_bytes(b'z')
        (tmp / 'note.txt').write_bytes(b'n')
        self.assemble_and_compare(['alpha'])
        self.assertEqual(self.leftovers(), ['keep', 'note.txt'])
        big = tmp / 'job-93'
        big.mkdir()
        for i in range(700):
            (big / ('%03d' % i)).write_bytes(b'')
        _, f = self.assemble_and_compare(['alpha'])
        left = len(list(big.iterdir()))
        self.assertTrue(0 < left < 700, left)
        print('\nsweep cap: %d of 700 files left; job %d steps' % (left, f['steps']), file=sys.stderr)

    def test_sweep_never_follows_a_link(self):
        """a symlink inside a leftover tmp/job-* folder (to a folder outside tmp/) is
        removed itself; the sweep never descends through it."""
        outside = self.root / 'user/keep'
        outside.mkdir(parents=True)
        (outside / 'song.bin').write_bytes(b'mine')
        leftover = self.root / TMP / 'job-91'
        leftover.mkdir(parents=True)
        (leftover / 'l').symlink_to(outside)
        self.assemble_and_compare(['alpha'])
        self.assertEqual((outside / 'song.bin').read_bytes(), b'mine')
        self.assertEqual(self.leftovers(), [])

    def test_cli_apply_wait_and_cancel(self):
        def cli(*argv):
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()), warnings.catch_warnings():
                warnings.simplefilter('ignore', ResourceWarning)
                code = host.main(['--sim', self.sim.address, 'mods'] + list(argv))
            return code, out.getvalue()
        self.dev.t.close()
        code, out = cli('apply', '--base', self.base, '--generation', '%s/%s' % (self.profile, self.generation),
                        'alpha', '--ext', 'cap', '--unverified', '--wait')
        self.assertEqual(code, 0, out)
        self.assertIn('job 1 started', out)
        self.assertIn('done 6/6', out)                                     # three content + three native files
        self.assertIn('library entry mods-a', out)                          # the entry id printed
        self.assertTrue((self.entry('mods-a') / 'os.ldr').is_file())
        code, out = cli('apply', '--base', self.base, '--generation', self.generation, '--profile', self.profile,
                        'alpha', '--wait')                                  # not confirmed: the base is unknown
        self.assertEqual(code, 1, out)
        self.assertIn('failed', out)
        self.assertIn('(base 18 base)', out)                                # error base, code unverified
        code, out = cli('cancel')
        self.assertEqual(code, ids.S_STATE, out)
        self.dev = host.Device(host.TcpPort(self.sim.address), on_log=None)


def _published():
    return ((PACKAGES / 'base/info.txt').is_file() and TK.STOCK.is_file() and TK.CATALOG.is_file() and
            MP.default_official(PACKAGES).is_file())


@unittest.skipIf(UNAVAILABLE, UNAVAILABLE)
@unittest.skipUnless(_published(), 'published-kit packages, stock os.ldr, op1_factory.json or content.sha256 absent')
class PublishedJob(Base, unittest.TestCase):
    """The real 0.0.6 package set on the real stock 1.2.1 os.ldr (a known base): 1 machine (fm2op), 7, and
    1 + both extensions."""
    base = 'opxy-121'

    @classmethod
    def setUpClass(cls):
        paths.SESSIONS.mkdir(parents=True, exist_ok=True)
        cls.tmp = tempfile.TemporaryDirectory(prefix='xyf-simjob-real-', dir=paths.SESSIONS)
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.work = Path(cls.tmp.name)
        cls.seed_with(PACKAGES, 'opxy-1.2.1', TK.STOCK.read_bytes(), TK.CATALOG.read_bytes(),
                      MP.default_official(PACKAGES).read_text())
        ids_ = [row[1] for row in M.read_table((PACKAGES / 'base/machines.txt').read_bytes())]
        cls.fit = M.fitting(PACKAGES, ids_)

    def test_entries_match_the_reference(self):
        self.assertEqual(self.fit[0], 'fm2op')
        for machines, exts in ((self.fit[:1], []), (self.fit[:7], []), (self.fit[:1], ['capture', 'master_dj_filter'])):
            with self.subTest(machines=len(machines), extensions=exts):
                self.assemble_and_compare(machines, exts)



class Served(unittest.TestCase):
    """mods apply --wait ends once file commands are served again (busy while B closes the window)."""
    def run_served(self, codes):
        class Dev:
            def list_page(self, path):
                self.paths.append(path)
                return codes.pop(0), [], 0
        dev, lines, now = Dev(), [], [0.0]
        dev.paths = []
        def sleep(s):
            now[0] += s
        return MS.served(dev, lines.append, sleep=sleep, clock=lambda: now[0]), lines, dev.paths

    def test_busy_then_served(self):
        code, lines, paths = self.run_served([ids.S_BUSY] * 6 + [0])
        self.assertEqual((code, paths[0]), (0, MS.TMP))
        self.assertEqual(lines, ['storage window closed 1.5 s after the job ended'])
        self.assertEqual(self.run_served([0])[:2], (0, []))

    def test_still_busy_gives_up(self):
        code, lines, _ = self.run_served([ids.S_BUSY] * 1000)
        self.assertIsNone(code)
        self.assertIn('still busy', lines[-1])


class SpecNames(unittest.TestCase):
    def test_host_names_are_the_spec_values(self):
        spec = {c['name']: c for c in json.loads((ROOT / 'protocol/v1.json').read_text())['commands']}
        values = lambda command, kind, field: [v for _, v in sorted(                       # noqa: E731
            (int(k), v) for k, v in next(f for f in spec[command][kind]['fields'] if f['name'] == field)['values'].items())]
        self.assertEqual(list(MS.PHASES), values('job.status', 'reply', 'phase'))
        self.assertEqual(list(MS.ERRORS), values('job.status', 'reply', 'error'))
        self.assertEqual(len(MS.MISSING), len(values('mods.apply', 'reply', 'missing')))
        payload = MS.apply_request('opxy-121', 'opxy-1.2.1', 'g', ['a', 'b'], ['c'], 'N', True)
        self.assertEqual(payload[:-32], b'opxy-121\0opxy-1.2.1\0g\0a,b\0c\0N\0\x01\0\0\0')
        self.assertEqual(payload[-32:], hashlib.sha256(payload[:-32]).digest())


if __name__ == '__main__':
    unittest.main()
