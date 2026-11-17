"""The stepped mod assembly job (decision 019; native/mod_job, host/mod_assemble.cpp
job|faults).

The job runs the device's linker, patch and content steps in bounded steps inside one work region (8 MiB,
filled with garbage), reading and writing through a file-call table (on the host: an in-memory file system
backed by the real files, folders only through the job's mkdir) and leaves one whole library entry. Its
folder must hold exactly modpatch.entry_files of the one-shot assembler's output (host/mod_assemble.cpp run,
itself proven against core.mjs by tests/test_modpatch.py): os.ldr and every content file byte-identical,
plus native info.txt (the strict reader's) and files.sha256 over all of them. Faults (a read or write
failure at every step, cancel at every step, a region one byte short, missing, truncated and short files, a
missing or damaged content file, an os.ldr that fails start_verify) must end in their error code with no
output name left and the same Job reusable. Synthetic packages always; the published 0.0.6 package set when
present; the Blackfin compile when the toolchain is present."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
from xy_forever import hostbuild, modkit as M, modpatch as P, paths, xyf_entry  # noqa: E402
from test_modpatch import CATALOG, OFFICIAL, PACKAGES, finish, kit_for, official_text, profile_for, stock  # noqa: E402
import test_modkit as TK  # noqa: E402
from test_xyf_freestanding import code_bytes, compiler  # noqa: E402

EXHAUSTIVE = bool(os.environ.get('XYF_EXHAUSTIVE'))
REGION = 8 << 20
DEVICE_REGION = (8 << 20) - (64 << 10)   # B's job region less the job's state (usb_service.cpp job_slot_bytes)
CHUNK = 64 * 1024
SLICE = 128 * 1024


def run(binary, *args):
    result = subprocess.run([str(binary), *map(str, args)], capture_output=True, text=True)
    if result.returncode:
        raise AssertionError(result.stderr[-2000:] + result.stdout[-2000:])
    return json.loads(result.stdout)


class Harness:
    def job(self, machines, exts=(), **kw):   # kw: job_text's
        folder = Path(tempfile.mkdtemp(dir=self.work))
        (folder / 'job.txt').write_text(self.job_text(list(machines), list(exts), **kw))
        (folder / 'out').mkdir()
        return run(self.binary, 'job', folder / 'job.txt', folder / 'out'), folder / 'out'

    def expected(self, out, machines, exts, memory='standard'):
        """The entry the job must leave: the one-shot assembly of the same job.txt, completed by entry_files."""
        (out.parent / 'ref').mkdir()
        report = P.run(self.binary, out.parent / 'job.txt', out.parent / 'ref')
        self.assertEqual([report[k] for k in ('input', 'patch', 'verify', 'content', 'copy')], ['ok'] * 5, report)
        return P.entry_files(self.gen, P.tree(out.parent / 'ref'), machines)

    def faults(self, machines, exts=(), stride=1):
        folder = Path(tempfile.mkdtemp(dir=self.work))
        (folder / 'job.txt').write_text(self.job_text(list(machines), list(exts)))
        return run(self.binary, 'faults', folder / 'job.txt', stride)

    def assert_identical(self, report, out, machines, exts, memory='standard'):
        self.assertEqual((report['error'], report['phase'], report['reference']), ('none', 'done', True), report)
        self.assertTrue(report['ldr_identical'] and report['catalog_identical'] and report['list_identical'], report)
        self.assertEqual(report['open_handles'], 0)
        tree, want = P.tree(out), self.expected(out, machines, exts, memory)
        self.assertEqual(sorted(tree), sorted(want))                  # finals only: no .part left
        for path in want:
            self.assertEqual(tree[path], want[path], path)
        xyf_entry.parse_manifest(tree['info.txt'])                    # the strict reader accepts it
        self.assertNotIn('layout.json', tree)
        self.assertEqual(xyf_entry.parse_manifest(tree['info.txt'])['machines'], len(machines))
        listed = {path: digest for digest, path in xyf_entry.read_files_sha256(tree['files.sha256'])}
        self.assertEqual(set(listed), set(tree) - {'files.sha256'})
        self.assertTrue(all(M.sha256(tree[path]) == digest for path, digest in listed.items()))
        self.assertLessEqual(report['peak'], DEVICE_REGION)
        for phase in ('inputs', 'write'):                             # one read or write call of <= 64 KiB
            self.assertLessEqual(report['largest'][phase], CHUNK, phase)
        self.assertLessEqual(report['largest']['verify'], SLICE)      # a read-back call, or one image-check slice
        self.assertLessEqual(report['largest']['link'], 1 << 20)       # one object (elf max_bytes) or zero slice
        self.assertLessEqual(report['largest']['patch'], SLICE)        # a patch or CRC-32 slice
        return tree

    def assert_faults(self, report, stride):
        self.assertEqual(report['violations'], [])
        steps = report['steps']
        self.assertEqual(report['counts']['cancel'], len(range(1, steps, stride)))
        self.assertGreater(report['counts']['read'], 0)
        self.assertGreater(report['counts']['write'], 0)


class Synthetic(Harness, unittest.TestCase):
    """Packages split by modkit from tests/test_modpatch.py's synthetic kit (an unverified base, confirmed)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='xyf-modjob-')
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.work = Path(cls.tmp.name)
        cls.binary = P.build(cls.work / 'bin', run=hostbuild.run)
        data = stock()
        (cls.work / 'os.ldr').write_bytes(data)
        (cls.work / 'op1_factory.json').write_bytes(CATALOG)
        (cls.work / 'content.sha256').write_text(official_text(OFFICIAL))
        (cls.work / 'kit.json').write_text(json.dumps(kit_for(profile_for(data))))
        cls.gen = Path(M.split_kit(cls.work / 'kit.json', cls.work / 'packages', finish=finish,
                                   sdk_source='https://github.com/example/sdk@abc')['root'])

    def job_text(self, machines, exts, profiling=False):
        return P.job_text(self.gen, machines, exts, self.work / 'os.ldr', self.work / 'op1_factory.json', profiling,
                          official=self.work / 'content.sha256', unverified=True)

    def test_matches_the_one_shot_assembler(self):
        for machines, exts, profiling in ((['alpha'], [], False), (['alpha', 'beta', 'gamma', 'delta'], [], False),
                                          (['alpha', 'gamma'], ['cap', 'flt'], False), (['delta', 'alpha'], ['flt'], True)):
            with self.subTest(machines=machines, extensions=exts):
                report, out = self.job(machines, exts, profiling=profiling)
                self.assert_identical(report, out, machines, exts)

    def test_faults_at_every_step(self):
        report = self.faults(['alpha', 'gamma'], ['cap', 'flt'])
        self.assert_faults(report, 1)
        self.assertIn('case missing content file', report['counts'])

    def test_refusals_leave_nothing(self):
        for machines, error in ((['../alpha'], 'argument'), (['alpha', 'nosuch'], 'open'), (['alpha', 'alpha'], 'link')):
            with self.subTest(machines=machines):
                report, out = self.job(machines)
                self.assertEqual((report['phase'], report['error']), ('failed', error), report)
                self.assertEqual(P.tree(out), {})


class JobFuzz(unittest.TestCase):
    """Damaged copies of every file the job reads (deterministic seed; hashes and pins mostly refreshed) run
    through the stepped job in the sanitizer build: each must end done or failed, no output left, no handle
    open, within a step cap; any memory error aborts the bridge. 400 rounds always (UBSan found work-region
    tables of pointers misaligned on the 64-bit host here); XYF_FUZZ=1: about 2 minutes (~60000 rounds)."""

    def test_fuzz(self):
        with tempfile.TemporaryDirectory(prefix='xyf-modjob-fuzz-') as tmp:
            work = Path(tmp)
            binary = P.build(work / 'bin', flags=P.SANITIZE, run=hostbuild.run)
            data = stock()
            (work / 'os.ldr').write_bytes(data)
            (work / 'op1_factory.json').write_bytes(CATALOG)
            (work / 'content.sha256').write_text(official_text(OFFICIAL))
            (work / 'kit.json').write_text(json.dumps(kit_for(profile_for(data))))
            gen = Path(M.split_kit(work / 'kit.json', work / 'packages', finish=finish,
                                   sdk_source='https://github.com/example/sdk@abc')['root'])
            (work / 'job.txt').write_text(P.job_text(gen, ['alpha', 'gamma'], ['cap', 'flt'], work / 'os.ldr',
                                                     work / 'op1_factory.json', official=work / 'content.sha256',
                                                     unverified=True))
            long = bool(os.environ.get('XYF_FUZZ'))
            seconds = float(os.environ.get('XYF_FUZZ_SECONDS', '110' if long else '60'))
            report = run(binary, 'jobfuzz', work / 'job.txt', os.environ.get('XYF_FUZZ_SEED', '54'),
                         1000000 if long else 400, seconds)
        if long:
            print('\njob fuzz:', json.dumps(report), file=sys.stderr)
        self.assertEqual(report['violations'], [])
        self.assertGreaterEqual(report['rounds'], 400)
        self.assertIn('done none', report['counts'])
        self.assertGreater(len(report['counts']), 6)

def _published():
    return ((PACKAGES / 'base/info.txt').is_file() and TK.STOCK.is_file() and TK.CATALOG.is_file() and
            P.default_official(PACKAGES).is_file())


@unittest.skipUnless(_published(), 'published-kit packages, stock os.ldr, op1_factory.json or content.sha256 absent')
class PublishedPackages(Harness, unittest.TestCase):
    """The real 0.0.6 package set: 1, 7 and 16 machines (modkit fitting, in collection order) and one machine
    with both extensions."""

    @classmethod
    def setUpClass(cls):
        paths.SESSIONS.mkdir(parents=True, exist_ok=True)
        cls.tmp = tempfile.TemporaryDirectory(prefix='mod-job-real-', dir=paths.SESSIONS)
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.work = Path(cls.tmp.name)
        cls.binary = P.build(cls.work / 'bin', run=hostbuild.run)
        ids = [row[1] for row in M.read_table((PACKAGES / 'base/machines.txt').read_bytes())]
        cls.fit = M.fitting(PACKAGES, ids)

    gen = PACKAGES

    def job_text(self, machines, exts, memory='standard'):
        return P.job_text(PACKAGES, machines, exts, TK.STOCK, TK.CATALOG, memory=memory)

    def test_expanded_memory(self):
        """The 4 MiB option on the device job: the entry is the one-shot assembly's (itself proven against
        core.mjs, test_modpatch PublishedKitExpanded), native metadata has the count, and
        the larger image still fits B's region (the image is kept last; link files use the module's free part)."""
        from test_modpatch import LARGE, NEAR
        for name, machines in (('large', LARGE), ('near', NEAR)):
            with self.subTest(name):
                report, out = self.job(machines, [], memory='expanded')
                tree = self.assert_identical(report, out, machines, [], memory='expanded')
                self.assertLessEqual(report['peak'], DEVICE_REGION)
        report, out = self.job(LARGE, [])
        self.assertEqual((report['error'], report['phase']), ('link', 'failed'))     # standard: does not fit
        self.assertFalse(any(out.iterdir()))

    def test_selections_match_the_one_shot_assembler(self):
        self.assertEqual(len(self.fit), 16)
        for name, machines, exts in (('1', self.fit[:1], []), ('7', self.fit[:7], []), ('16', self.fit, []),
                                     ('1+both extensions', self.fit[:1], ['capture', 'master_dj_filter'])):
            with self.subTest(name):
                report, out = self.job(machines, exts)
                tree = self.assert_identical(report, out, machines, exts)
                if 'fm2op' in machines:                                 # the content a mod firmware opens at boot
                    self.assertIn('content/display/fm2op.svg', tree)
                    self.assertIn('content/presets/fm2op/default.json', tree)
        self.assertEqual(self.fit[0], 'fm2op')

    def test_faults(self):
        stride = 1 if EXHAUSTIVE else 23                              # every step: ~25 s
        self.assert_faults(self.faults(self.fit[:1], stride=stride), stride)


class BlackfinCompile(unittest.TestCase):
    """native/mod_job.cpp at the firmware's flags: no calls outside xyf_core, no BSS or data, small frames;
    its text + rodata is measured (no bound: compile and assemble only)."""

    def test_freestanding(self):
        gxx = compiler()
        if not gxx:
            self.skipTest('Blackfin toolchain absent')
        from xy_forever import toolchain
        with tempfile.TemporaryDirectory(prefix='xyf-modjob-cross-') as tmp:
            asm = Path(tmp) / 'mod_job.s'
            subprocess.run([str(gxx), *toolchain.CXX_FLAGS, '-fstack-usage', '-S', str(ROOT / 'native/mod_job.cpp'),
                            '-o', str(asm)], check=True, capture_output=True, cwd=tmp)
            text = asm.read_text()
            calls = {c for c in re.findall(r'\bcall\s+(_[A-Za-z0-9_.]+)', text) if not c.startswith('__Z')}
            self.assertEqual(calls, set())
            self.assertNotRegex(text, r'\.(bss|lcomm|comm|data)\b')
            frames = [int(l.split('\t')[1]) for l in (Path(tmp) / 'mod_job.su').read_text().splitlines()]
            self.assertLess(max(frames), 1024)
            self.assertGreater(code_bytes(gxx, 'mod_job', toolchain.CXX_FLAGS, tmp), 0)


if __name__ == '__main__':
    unittest.main()
