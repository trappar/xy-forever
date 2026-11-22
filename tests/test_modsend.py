"""Mod packages sent to the device (xy_forever/modsend.py, `uv run xyf-host mods push|list|remove`),
against the device simulator. A push leaves the package set byte-identical, in the flat store (xy_forever/modstore.py),
under /yaffs2/xy-forever/mods/<generation>/; a second push sends nothing; a push cut off part-way never
leaves a package folder with files.sha256 unless every listed file is there and right, and running it again
completes it; --verify passes. The proof that matters: the host build of the device job (host/mod_assemble.cpp
`job`) run on the files as they sit in the simulator's storage gives exactly what it gives on the host
package set. Synthetic packages always; the published 0.0.6 set when present. No device access."""

import contextlib
import hashlib
import io
import json
import os
import shutil
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pytest
import test_modkit as TK
from test_mod_job import run
from test_modpatch import CATALOG, OFFICIAL, PACKAGES, finish, kit_for, official_text, profile_for, stock

from xy_forever import hostbuild, modstore, package_records, sim
from xy_forever import modkit as M
from xy_forever import modpatch as MP
from xy_forever import modsend as S
from xy_forever import usb_proto as P
from xy_forever import xyf_host as host

ROOT = Path(__file__).resolve().parents[1]

UNAVAILABLE = sim.available() or (None if sim.STOCK_OS.is_file() else 'private stock 1.2.1 os.ldr absent')


class Cut(Exception):
    pass


class CutDevice(host.Device):
    """Drops the connection after `after` file writes (the device keeps what it wrote)."""

    def __init__(self, transport, after):
        super().__init__(transport, on_log=None)
        self.after = after

    def call(self, kind, payload=b'', **kw):
        if kind == P.ids.WRITE_BEGIN:
            self.after -= 1
            if self.after < 0:
                self.t.close()
                raise Cut('streamed package write')
        return super().call(kind, payload, **kw)


def store_of(gen, names=None):
    """{device path below the generation folder: bytes} a push of gen (names: these packages only) leaves."""
    out = {modstore.FILE: modstore.TEXT}
    for name in sorted(os.listdir(gen)):
        if (Path(gen) / name / S.LIST).is_file() and (names is None or name in names):
            files = sorted((r, b) for r, b in MP.tree(Path(gen) / name).items() if r != S.LIST)
            files.append((S.LIST, (Path(gen) / name / S.LIST).read_bytes()))
            out.update(('%s/%s' % (name, s), b) for s, _, b in modstore.stored_files(name, files))
    return out


class Harness:
    @classmethod
    def start_class(cls, work):
        cls.work = Path(work)
        cls.out = sim.build()
        cls.seeded = sim.seed(cls.work / 'seed')
        cls.binary = MP.build(cls.work / 'bin', run=hostbuild.run)
        cls.runs = 0

    def setUp(self):
        type(self).runs += 1
        self.dir = self.work / ('run-%d' % self.runs)
        shutil.copytree(self.seeded, self.dir / 'yaffs2')
        self.sim = sim.Simulator(
            self.dir / 'yaffs2', self.dir / 'bootpart.bin', build_dir=self.out, log=self.dir / 'log.txt'
        ).start()
        self.links = []
        self.addCleanup(self.stop)
        info = MP.info_values(self.gen / 'base/info.txt')
        self.stored = self.dir / 'yaffs2/xy-forever/mods' / info['generation']

    def stop(self):
        for link in self.links:
            link.t.close()
        self.sim.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    def connect(self, after=None):
        transport = host.TcpPort(self.sim.address)
        dev = host.Device(transport, on_log=None) if after is None else CutDevice(transport, after)
        self.links.append(dev)
        self.assertEqual(dev.hello()[0], 0)
        return dev

    def push(self, dev=None, **kw):
        lines = []
        total = S.push(dev or self.connect(), self.gen, out=lines.append, **kw)
        return total, lines

    def assert_stored(self, names=None):
        """The device's generation folder holds exactly the host's packages (names: these only), stored flat."""
        want = store_of(self.gen, names)
        stored = MP.tree(self.stored)
        self.assertEqual(sorted(stored), sorted(want))
        self.assertEqual([p for p in want if stored[p] != want[p]], [])

    def assert_nothing_false_complete(self):
        """Every package folder holding files.sha256 has every listed file with its listed SHA-256."""
        for folder in self.stored.iterdir() if self.stored.is_dir() else ():
            if (folder / S.LIST).is_file():
                for sha, rel in S.list_paths((folder / S.LIST).read_bytes()):
                    stored = folder / modstore.stored_name(rel, sha)
                    self.assertTrue(stored.is_file(), (folder.name, rel))
                    self.assertEqual(hashlib.sha256(stored.read_bytes()).hexdigest(), sha, (folder.name, rel))

    def job(self, generation, machines, exts):
        """The host build of the device job over `generation`: (report, {path: bytes} of its entry)."""
        folder = Path(tempfile.mkdtemp(dir=self.work))
        (folder / 'job.txt').write_text(self.job_text(generation, machines, exts))
        (folder / 'out').mkdir()
        report = run(self.binary, 'job', folder / 'job.txt', folder / 'out')
        return report, MP.tree(folder / 'out')

    def assert_job_identical(self, machines, exts):
        on_host = self.job(self.gen, machines, exts)
        on_device = self.job(self.stored, machines, exts)
        for report, _ in (on_host, on_device):
            self.assertEqual((report['error'], report['phase']), ('none', 'done'), report)
        self.assertIn('os.ldr', on_device[1])
        self.assertEqual(sorted(on_device[1]), sorted(on_host[1]))
        for path in on_host[1]:
            self.assertEqual(on_device[1][path], on_host[1][path], path)


@unittest.skipIf(UNAVAILABLE, UNAVAILABLE)
class Synthetic(Harness, unittest.TestCase):
    """Packages split by modkit from tests/test_modpatch.py's synthetic kit (as tests/test_mod_job.py)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='xyf-modsend-')
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.start_class(cls.tmp.name)
        data = stock()
        (cls.work / 'os.ldr').write_bytes(data)
        (cls.work / 'op1_factory.json').write_bytes(CATALOG)
        (cls.work / 'content.sha256').write_text(official_text(OFFICIAL))
        (cls.work / 'kit.json').write_text(json.dumps(kit_for(profile_for(data))))
        cls.gen = Path(
            M.split_kit(
                cls.work / 'kit.json',
                cls.work / 'packages',
                finish=finish,
                sdk_source='https://github.com/example/sdk@abc',
            )['root']
        )

    def job_text(self, generation, machines, exts):
        return MP.job_text(
            generation,
            machines,
            exts,
            self.work / 'os.ldr',
            self.work / 'op1_factory.json',
            official=self.work / 'content.sha256',
            unverified=True,
        )

    @pytest.mark.sweep
    def test_a_cut_push_never_looks_complete_and_a_second_push_completes_it(self):
        files = len(store_of(self.gen))  # store.txt first, then the packages
        for after in (0, 1, 5, files // 2, files - 1):
            with self.subTest(after=after):
                with self.assertRaises(Cut):
                    self.push(self.connect(after=after))
                self.sim.advance(11000)
                self.assert_nothing_false_complete()
                total, _ = self.push()
                self.assertGreater(total['files'], 0)
                self.assertLessEqual(total['files'], files - 1)
                self.assert_stored()
                shutil.rmtree(self.stored)
        # a file replaced part-way (file.write_staged truncates, then writes) in a complete package:
        # the list goes first, and the next push sends the file and the list again
        self.push()
        victim = self.stored / 'alpha' / 'catalog.txt'
        victim.write_bytes(victim.read_bytes()[:5])
        with self.assertRaises(Cut):
            self.push(self.connect(after=0))
        self.sim.advance(11000)
        self.assertTrue((self.stored / 'alpha' / S.LIST).exists())
        self.assertEqual(victim.read_bytes(), victim.read_bytes()[:5])
        total, lines = self.push()
        self.assertEqual((total['sent'], total['files']), (1, len(MP.tree(self.gen / 'alpha'))))
        self.assert_stored()
        self.assert_job_identical(['alpha'], [])


class StoredNames(unittest.TestCase):
    """xy_forever/modstore.py (native/mod_job.hpp stored_name and clients/ts/src/modstore.ts are the same rule)."""

    def test_names(self):
        sha = 'ab' * 32
        for rel, want in (
            ('info.txt', 'info.txt'),
            ('content/presets/acidline/x.json', 'abababababab.json'),
            ('objects/sdk/runtime/_muldi3.o', 'abababababab.o'),
            ('a/.hidden', 'abababababab'),
            ('a/b.tar.gz', 'abababababab.gz'),
            ('a/b.toolong8', 'abababababab'),
            ('a/b.j-s', 'abababababab'),
            ('a/noext', 'abababababab'),
            ('a/b.SVG', 'abababababab.SVG'),
        ):
            self.assertEqual(modstore.stored_name(rel, sha), want, rel)

    def test_same_content_shares_a_name_and_a_clash_is_refused(self):
        same = modstore.stored_files('p', [('a/x.o', b'1'), ('b/y.o', b'1'), (S.LIST, b'')])
        self.assertEqual([r for _, r, _ in same], ['a/x.o', S.LIST])
        with mock.patch.object(modstore, 'stored_name', lambda rel, sha: 'n.o' if '/' in rel else rel):
            with self.assertRaises(modstore.Clash):
                modstore.stored_files('p', [('a/x.o', b'1'), ('b/y.o', b'2')])

    def test_version(self):
        self.assertEqual(
            [
                modstore.version_of(b)
                for b in (
                    modstore.TEXT,
                    b'version=1',
                    b'version=2\r\n',
                    b'version=',
                    b'version=1x',
                    b'',
                    None,
                    b'Version=1',
                )
            ],
            [1, 1, 2, 0, 0, 0, 0, 0],
        )


def _published():
    return (
        package_records.local_name(PACKAGES / 'base')
        and TK.STOCK.is_file()
        and TK.CATALOG.is_file()
        and MP.default_official(PACKAGES).is_file()
    )


@unittest.skipUnless(package_records.local_name(PACKAGES / 'base'), 'published-kit packages absent (setup.py fixtures)')
class RealSetDepth(unittest.TestCase):
    """#124 over the real package set (setup.py doctor: "mod package set"): every device path a push writes sits 4
    folders below /yaffs2 (xy-forever/mods/<generation>/<package>/<stored name>), 2 under the limit of 6 (the stock
    dev-menu MTP's); the assembled entry's content, which the OS reads as content/presets/<id>/<file>, is at most 6
    deep (tmp/job-<id>-old/ and firmware/<id>/), at the limit."""

    def test_every_device_path_keeps_the_depth_rule(self):
        from xy_forever import depth

        _, generation, packages = S.local_set(PACKAGES)
        paths_ = [
            '%s/%s/%s/%s' % (S.ROOT, generation, name, stored)
            for name, files in packages.items()
            for stored, _, _ in modstore.stored_files(name, files)
        ] + ['%s/%s/%s' % (S.ROOT, generation, modstore.FILE)]
        self.assertGreater(len(paths_), 1000)
        self.assertEqual(depth.MAX_FOLDERS, 6)
        self.assertEqual({depth.folders(p) for p in paths_ if not p.endswith('/' + modstore.FILE)}, {4})
        self.assertEqual([p for p in paths_ if depth.refusal(p)], [])
        self.assertLessEqual(max(len(p.rsplit('/', 1)[1]) for p in paths_), 17)  # 12 hex digits + ".json"
        entry = '/yaffs2/xy-forever/tmp/job-4294967295-old'  # the deepest folder an entry passes through
        content = [rel for files in packages.values() for rel, _ in files if rel.startswith('content/')]
        self.assertTrue(content)
        self.assertEqual([r for r in content if depth.refusal('%s/%s' % (entry, r))], [])
        self.assertEqual([r for r in content if depth.refusal('/yaffs2/' + r)], [])


class WaitAllowance(unittest.TestCase):
    """193: job.status unanswered while B waits in one request to A: wait asks again (QUIET), then gives up."""

    def run_wait(self, silent_calls):
        clock = [0.0]
        replies = []

        def fake_status(dev, job):
            replies.append(job)
            if len(replies) <= silent_calls:
                clock[0] += 5.0  # one call's timeout
                raise TimeoutError('no reply to 0x45')
            return 0, {
                'phase': S.DONE,
                'steps': 1,
                'detail': 'x',
                'done': 1,
                'total': 1,
                'error': 0,
                'code': 0,
                'elapsed_ms': 0,
                'longest_ms': 0,
                'debris': 0,
            }

        lines = []
        with mock.patch.object(S, 'job_status', fake_status):
            return S.wait(None, 0, out=lines.append, sleep=lambda s: None, clock=lambda: clock[0]), lines

    def test_a_late_first_answer_is_waited_for(self):
        f, lines = self.run_wait(3)  # 15 s without an answer
        self.assertEqual(f['phase'], S.DONE)
        self.assertEqual(sum('no answer yet' in l for l in lines), 1)

    def test_silence_past_the_allowance_raises(self):
        with self.assertRaises(TimeoutError):
            self.run_wait(int(S.QUIET // 5) + 2)


class ApplySelection(unittest.TestCase):
    """`mods apply`'s selection on the host (no device, no simulator): a selection once
    passed its ids as one shell word (zsh does not split an unquoted variable), which the device answered
    bad_request as one invalid id; split, they are the 16 to 30 ids the device builds. A selection the
    device would refuse is refused before connecting, naming the limit. Synthetic ids."""

    class Dev:
        def __init__(self):
            self.payloads = []

        def call(self, type_, payload, timeout=None):
            self.payloads.append((type_, payload))
            return 0, struct.pack('<II', 7, 0)

    proto = '1.16'

    def run_apply(self, words, *options):
        import argparse

        parser = argparse.ArgumentParser()
        parser.add_argument('--timeout', type=float, default=5.0)
        S.add_arguments(parser.add_subparsers(dest='command').add_parser('mods'))
        args = parser.parse_args(
            ['mods', 'apply', '--base', 'opxy-121-stock', '--generation', 'opxy-1.2.1/g1'] + list(words) + list(options)
        )
        dev, connected = self.Dev(), []

        def connect(timeout):
            connected.append(timeout)
            return dev, 'fake', 'XYF1;service=usb;role=b;proto=%s' % self.proto

        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            code = S.command(args, connect)
        return code, printed.getvalue(), dev.payloads, connected

    def fields(self, payload):
        return payload[:-36].split(b'\0')

    def test_an_older_device_is_refused_before_anything_is_sent(self):
        """#124: a device before protocol 1.16 reads mods/<profile>/<generation>/; push and apply refuse it."""
        self.proto = '1.15'
        code, out, payloads, _ = self.run_apply(['m1'])
        self.assertEqual((code, payloads), (2, []))
        self.assertIn('protocol 1.16 or later', out)
        self.assertEqual(
            [S.layout_ok(v) for v in ('1.16', '1.20', '2.0', '1.15', '1', 'x')], [True, True, True, False, False, False]
        )

    def test_ids_in_one_word_are_split(self):
        ids = ['m%02d' % i for i in range(30)]
        for words in ([' '.join(ids)], [','.join(ids)], ids[:10] + [' '.join(ids[10:20]), ','.join(ids[20:])]):
            code, out, payloads, _ = self.run_apply(words, '--name', 'Measure 30', '--ext', 'e1 e2')
            self.assertEqual(code, 0, out)
            ((type_, payload),) = payloads
            self.assertEqual(type_, P.ids.MODS_APPLY)
            self.assertEqual(
                payload, S.apply_request('opxy-121-stock', 'opxy-1.2.1', 'g1', ids, ['e1', 'e2'], 'Measure 30')
            )
            self.assertEqual(self.fields(payload)[3], ','.join(ids).encode())

    def test_the_largest_selection_is_sent(self):
        ids = ['m%014d' % i for i in range(64)]  # 64 ids of 15 characters: 1023 bytes
        exts = ['e%030d' % i for i in range(8)]  # 8 of 31: 255 bytes
        code, out, payloads, _ = self.run_apply(ids, '--ext', *exts, '--name', 'N' * 47)
        self.assertEqual(code, 0, out)
        self.assertEqual(len(self.fields(payloads[0][1])[3]), 1023)

    def test_a_selection_the_device_refuses_is_refused_first(self):
        cases = (
            (['m%02d' % i for i in range(65)], (), '65 machines: at most 64'),
            (['m%015d' % i for i in range(64)], (), 'the machine list is 1087 bytes: at most 1023'),
            (['a'], ['e%d' % i for i in range(9)], '9 extensions: at most 8'),
            (['a', 'Bad'], (), "machine id 'Bad'"),
            (['a', 'b', 'a'], (), 'machine a given twice'),
            (['a'], ['x' * 32], "extension id '%s'" % ('x' * 32)),
            ([','], (), 'no machine'),
        )
        for machines, exts, why in cases:
            code, out, payloads, connected = self.run_apply(machines, *(('--ext',) + tuple(exts) if exts else ()))
            self.assertEqual((code, payloads, connected), (2, [], []), out)
            self.assertIn('mods apply: refused: ' + why, out)
        code, out, payloads, connected = self.run_apply(['a'], '--name', ' x')
        self.assertEqual((code, connected), (2, []))
        self.assertIn('name', out)


if __name__ == '__main__':
    unittest.main()
