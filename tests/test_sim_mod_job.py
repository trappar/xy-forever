"""The mod assembly job on processor B in the simulator (mods.apply, job.status, job.cancel;
the commit into library entry mods-a / mods-b, "content not installed", the tmp/job-* sweep).

B runs native/mod_job from its menu loop over its timed file client; the entry it leaves in
/yaffs2/xy-forever/tmp/job-<id>/ must equal test_mod_job.py's reference (the one-shot host assembly,
completed by modpatch.entry_files) byte for byte, and nothing outside that folder may change. Faults
(cancel, a failed storage write, A going silent) end with no os.ldr, the write window closed and B
answering; a later job works. Synthetic packages always; the published 0.0.6 set when present. Needs
clang++ and the private stock 1.2.1 images (skipped without them). No device access."""

import hashlib
import json
import shutil
import struct
import sys
import tempfile
import time
import unittest
import zlib
from pathlib import Path

import pytest
import test_modkit as TK
from test_modpatch import CATALOG, OFFICIAL, PACKAGES, finish, kit_for, official_text, profile_for, stock

from xy_forever import bounds, census, modstore, package_records, sim
from xy_forever import modkit as M
from xy_forever import modpatch as MP
from xy_forever import modsend as MS
from xy_forever import usb_proto as P
from xy_forever import usb_proto_ids as ids
from xy_forever import xyf_host as host
from xy_forever.startup_ready import wait_startup_ready

ROOT = Path(__file__).resolve().parents[1]

UNAVAILABLE = sim.available() or (None if sim.STOCK_OS.is_file() else 'private stock 1.2.1 os.ldr absent')
NAME = 'OP-XY mods'
TMP = 'xy-forever/tmp'
LIB = 'xy-forever/firmware'
FILE_COMMAND = (P.READ_FILE, b'/yaffs2/xy-forever/state-a.txt\0')


def tree(folder):
    folder = Path(folder)
    return (
        {p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob('*') if p.is_file()}
        if folder.exists()
        else {}
    )


def outside_tmp(root):
    """Every file and folder of the storage but tmp/ and the reports (what the job must never touch)."""
    root = Path(root)
    keep = {}
    for p in sorted(root.rglob('*')):
        rel = p.relative_to(root).as_posix()
        if rel.startswith((TMP, 'xy-forever/log', 'xy-forever/blobs', LIB + '/mods-')):
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
        modstore.copy_set(gen_dir, cls.seeded / 'xy-forever/mods' / gen_dir.name)  # the flat store (#124)
        entry = cls.seeded / 'xy-forever/firmware' / cls.base
        entry.mkdir(parents=True, exist_ok=True)
        (entry / 'os.ldr').write_bytes(stock_bytes)
        (entry / 'info.txt').write_bytes(sim.info_text(stock_bytes, 'Base', '1.2.1'))
        (entry / 'content.sha256').write_text(official_text_)
        (cls.seeded / 'content/op1_factory.json').write_bytes(catalog)
        cls.profile, cls.generation, cls.gen_dir = profile, gen_dir.name, gen_dir
        cls.binary = MP.build()
        cls.runs = 0

    def setUp(self):
        type(self).runs += 1
        self.dir = self.work / ('run-%d' % self.runs)
        self.root = self.dir / 'yaffs2'
        shutil.copytree(self.seeded, self.root)
        if getattr(self, 'settings_text', None):  # settings.txt for this test
            (self.root / 'xy-forever/settings.txt').write_bytes(self.settings_text)
        self.sim = sim.Simulator(self.root, self.dir / 'bootpart.bin', log=self.dir / 'log.txt').start()
        self.dev = host.Device(host.TcpPort(self.sim.address), on_log=None)
        self.assertEqual(self.dev.hello()[0], 0)
        # These fixtures begin with an admitted job; boot/recovery observations use later reconnects.
        wait_startup_ready(
            self.dev.hello_info.text,
            lambda remaining: self.dev.call(ids.STORAGE_SPACE, struct.pack('<I', 1), timeout=remaining)[0],
        )

    def tearDown(self):
        self.dev.t.close()
        self.sim.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    # -- helpers ---------------------------------------------------------------------------------
    def start(self, machines, exts=(), **kw):
        code, job, missing = MS.start(
            self.dev, kw.pop('base', self.base), self.profile, self.generation, machines, exts, NAME, self.unverified
        )
        self.assertEqual((code, missing), (0, 0))
        self.assertGreater(job, 0)
        return job

    def status(self, job=0):
        code, f = MS.job_status(self.dev, job)
        self.assertEqual(code, 0)
        return f

    def finished(self, job, timeout=bounds.WAIT_S, served=True):
        deadline = time.monotonic() + timeout
        while True:
            f = self.status(job)
            if f['phase'] in (MS.DONE, MS.FAILED, MS.CANCELLED):
                if served:  # the window closed (as --wait)
                    self.assertIn(MS.served(self.dev, out=lambda line: None), (0, ids.S_NOT_FOUND))
                return f
            self.assertLess(time.monotonic(), deadline, 'job %d did not end: %s' % (job, f))
            self.sim.advance(20)

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
            data += body[4:]  # after the address word
        found = census.catalog(data)
        self.assertIsNotNone(found)
        return found

    def listed(self, name):
        """The catalog item of library entry name, or None."""
        items = [i for i in self.catalog()['items'] if i['id'] == name]
        return items[0] if items else None

    def until_swept(self, timeout=30.0):
        """The USB service's start sweep (#3) removed every tmp/job-* folder (bounded wait)."""
        end = time.monotonic() + timeout
        while [n for n in self.leftovers() if n.startswith('job-')]:
            self.assertLess(time.monotonic(), end, self.leftovers())
            self.sim.advance(50)

    def leftovers(self):
        tmp = self.root / TMP
        return sorted(p.name for p in tmp.iterdir()) if tmp.exists() else []

    def reference(self, machines, exts):
        folder = Path(tempfile.mkdtemp(dir=self.dir))
        job = folder / 'job.txt'
        job.write_text(
            MP.job_text(
                self.gen_dir,
                machines,
                exts,
                self.root / 'xy-forever/firmware' / self.base / 'os.ldr',
                self.root / 'content/op1_factory.json',
                official=self.root / 'xy-forever/firmware' / self.base / 'content.sha256',
                unverified=self.unverified,
                name=NAME,
            )
        )
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
        self.assertIn(f['detail'], ('mods-a', 'mods-b'), f)  # the library entry
        self.assertFalse(self.output(job).exists())  # moved into the library
        got, want = tree(self.entry(f['detail'])), self.reference(machines, exts)
        self.assertEqual(sorted(got), sorted(want))  # no .part, nothing else
        for path in want:
            self.assertEqual(got[path], want[path], path)
        for rel, data in want.items():
            if rel.startswith('content/'):
                digest = hashlib.sha256(data).hexdigest()
                self.assertEqual((self.root / 'xy-forever/blobs' / digest).read_bytes(), data)
        self.assertEqual(outside_tmp(self.root), before)  # nothing but tmp/ and the mods ids
        item = self.listed(f['detail'])  # listed at once (A rescanned)
        self.assertEqual((item['name'], item['usable'], item['content_missing']), (NAME, False, True), item)
        self.assertEqual(self.sim.status()['rw_open'], 0)  # the write window closed
        print(
            '\n%s: %d machine(s) + %d extension(s): %d steps, longest pass %d ms, %d ms device clock, %.2f s host, '
            'peak %d B'
            % (
                type(self).__name__,
                len(machines),
                len(exts),
                f['steps'],
                f['longest_ms'],
                f['elapsed_ms'],
                seconds,
                f['peak'],
            ),
            file=sys.stderr,
        )
        return job, f


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
        gen = Path(
            M.split_kit(
                cls.work / 'kit.json',
                cls.work / 'packages',
                finish=finish,
                sdk_source='https://github.com/example/sdk@abc',
            )['root']
        )
        cls.seed_with(gen, MP.info_values(gen / 'base/info.txt')['profile'], data, CATALOG, official_text(OFFICIAL))

    def reconnect(self):
        self.dev.t.close()
        self.dev = host.Device(host.TcpPort(self.sim.address), on_log=None)

    # -- the commit into the library, "content not installed", the sweep ---------------------
    def slot(self, default='opxy-121', once=''):
        """A newer state slot (state-b.txt, seq 2) naming the default and the boot-once entry."""
        body = ('default=%s\n%sseq=2\n' % (default, 'once=%s\n' % once if once else '')).encode()
        (self.root / 'xy-forever/state-b.txt').write_bytes(body + b'crc32=%08x\n' % (zlib.crc32(body) & 0xFFFFFFFF))

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
            self.sim.advance(50)

    def press(self, *keys):
        for key, pressed in keys:
            self.assertEqual(self.dev.call(P.KEY, struct.pack('<II', key, pressed))[0], 0)

    @pytest.mark.gate  # a simulator sweep
    @pytest.mark.uses('sim/')
    def test_power_cut_at_every_new_content_blob_publication(self):
        wanted = self.reference(['alpha'], [])
        count = len({hashlib.sha256(data).digest() for path, data in wanted.items() if path.startswith('content/')})
        before = outside_tmp(self.root)
        for offset in range(1, count + 1):
            with self.subTest(blob=offset):
                shutil.rmtree(self.root / 'xy-forever/blobs', ignore_errors=True)
                cuts = self.sim.status()['cuts']
                self.fault(len(wanted) + offset, 2, ['alpha'])
                self.wait_sim(lambda state: state['cuts'] == cuts + 1 and state['listening'], 'blob cut and restart')
                self.reconnect()
                self.assertEqual(self.dev.hello()[0], 0)
                self.sim.set('rename_fault', 0)
                self.until_swept()
                self.assertFalse(self.entry('mods-a').exists())
                self.assertFalse(self.entry('mods-b').exists())
                self.assertEqual(outside_tmp(self.root), before)
                for blob in (self.root / 'xy-forever/blobs').glob('*'):
                    self.assertEqual(blob.name, hashlib.sha256(blob.read_bytes()).hexdigest())
        self.assemble_and_compare(['alpha'])

    @pytest.mark.gate  # a simulator sweep
    @pytest.mark.uses('sim/')
    def test_power_cut_at_each_commit_step_then_the_sweep(self):
        """A power cut right after the rename in (the new entry listed) and right after the rename away (the
        target missing, the other entry intact); the restarted USB service sweeps tmp/job-* (the old tree, the
        unfinished output; #3), never anything else, and the next job finds nothing to sweep."""
        files = self.two_entries(['alpha'])
        (self.root / TMP / 'keep').mkdir()
        cuts = self.sim.status()['cuts']
        for n, target, other in ((files + 2, 'mods-b', 'mods-a'), (files + 1, 'mods-a', 'mods-b')):
            with self.subTest(n=n - files):  # after a cut no last write is known: mods-a
                kept = tree(self.entry(other))
                self.fault(n, 2, ['alpha'])
                cuts += 1
                self.wait_sim(lambda s: s['cuts'] == cuts and s['listening'], 'power cut and restart')
                self.reconnect()
                self.assertEqual(self.dev.hello()[0], 0)
                self.until_swept()  # the USB service's start (#3)
                self.assertIn('keep', self.leftovers())
                self.assertEqual(tree(self.entry(other)), kept)
                if n == files + 2:  # the new entry, without the old marker
                    self.assertEqual(sorted(tree(self.entry(target))), sorted(tree(self.entry(other))))
                    self.assertTrue(self.listed(target))
                else:  # the target missing, not listed
                    self.assertFalse(self.entry(target).exists())
                    self.assertIsNone(self.listed(target))
                self.assertEqual(self.dev.read_file('/yaffs2/xy-forever/state-a.txt')[0], 0)
        self.assemble_and_compare(['alpha'])  # the next build works, sweeps first
        self.assertEqual(self.leftovers(), ['keep'])


def _published():
    return (
        package_records.local_name(PACKAGES / 'base')
        and TK.STOCK.is_file()
        and TK.CATALOG.is_file()
        and MP.default_official(PACKAGES).is_file()
    )


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
        values = lambda command, kind, field: [
            v
            for _, v in sorted(
                (int(k), v)
                for k, v in next(f for f in spec[command][kind]['fields'] if f['name'] == field)['values'].items()
            )
        ]
        self.assertEqual(list(MS.PHASES), values('job.status', 'reply', 'phase'))
        self.assertEqual(list(MS.ERRORS), values('job.status', 'reply', 'error'))
        self.assertEqual(len(MS.MISSING), len(values('mods.apply', 'reply', 'missing')))
        payload = MS.apply_request('opxy-121', 'opxy-1.2.1', 'g', ['a', 'b'], ['c'], 'N', True)
        self.assertEqual(payload[:-32], b'opxy-121\0opxy-1.2.1\0g\0a,b\0c\0N\0\x01\0\0\0')
        self.assertEqual(payload[-32:], hashlib.sha256(payload[:-32]).digest())


if __name__ == '__main__':
    unittest.main()
