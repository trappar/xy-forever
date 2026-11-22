"""The local mod package index (xy_forever/modindex.py, `xyf_package.py index build | verify`,
`xyf_host.py mods fetch`), on the synthetic package set of tests/test_modsend.py. A build is
deterministic (same bytes twice); verify passes and catches a changed archive; a fetch over http (a local
http.server), file:// and a plain folder rebuilds the generation folder byte for byte, and `mods push` of
the fetched folder leaves the simulator's copy identical to the original. Every refusal has a case.
Nothing is published and nothing from the real package set is used. No device access."""
import contextlib
import functools
import http.server
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import unittest
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
import xyf_host as host                                                     # noqa: E402
import xyf_package                                                          # noqa: E402
from xy_forever import modindex as I, modkit as M, modpatch as MP, modsend as S, modstore, sim  # noqa: E402
from test_modpatch import finish, kit_for, profile_for, stock               # noqa: E402

SIM_UNAVAILABLE = sim.available() or (None if sim.STOCK_OS.is_file() else 'private stock 1.2.1 os.ldr absent')


def quiet(*_):
    pass


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


@pytest.mark.sim
class Index(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='xyf-modindex-')
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.work = Path(cls.tmp.name)
        (cls.work / 'kit.json').write_text(json.dumps(kit_for(profile_for(stock()))))
        cls.gen = Path(M.split_kit(cls.work / 'kit.json', cls.work / 'packages', finish=finish,
                                   sdk_source='https://github.com/example/sdk@abc')['root'])
        cls.index = cls.work / 'index'
        with contextlib.redirect_stdout(io.StringIO()):
            assert xyf_package.main(['index', 'build', str(cls.gen), '--out', str(cls.index)]) == 0
        handler = functools.partial(Quiet, directory=str(cls.index))
        cls.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.addClassCleanup(cls.server.server_close)
        cls.addClassCleanup(cls.server.shutdown)
        cls.url = 'http://127.0.0.1:%d/' % cls.server.server_address[1]
        cls.runs = 0

    def fresh(self):
        type(self).runs += 1
        return self.work / ('out-%d' % self.runs)

    def copy(self):
        """A copy of the index folder to spoil; (folder, its index dict)."""
        folder = self.fresh()
        shutil.copytree(self.index, folder)
        return folder, json.loads((folder / 'index.json').read_text())

    def save(self, folder, index):
        (folder / 'index.json').write_text(json.dumps(index))

    def package(self, index, pid='alpha'):
        return next(p for p in index['profiles'][0]['generations'][0]['packages'] if p['id'] == pid)

    def refused(self, where, text, **kw):
        with self.assertRaises(I.Refused) as caught:
            I.fetch(where, self.fresh(), out=quiet, **kw)
        self.assertIn(text, str(caught.exception))

    def rearchive(self, folder, index, pid, members, compress=zipfile.ZIP_STORED):
        """Replaces pid's archive by one holding members ({name: bytes}) and updates its size and hash
        in the index, so only the archive's content is wrong."""
        out = io.BytesIO()
        with zipfile.ZipFile(out, 'w', compress) as z:
            for name, body in members.items():
                z.writestr(name, body)
        data = out.getvalue()
        pkg = self.package(index, pid)
        (folder / pkg['archive']['name']).write_bytes(data)
        pkg['archive'].update(size=len(data), sha256=I.sha256(data))
        self.save(folder, index)

    def members(self, folder, index, pid='alpha'):
        with zipfile.ZipFile(folder / self.package(index, pid)['archive']['name']) as z:
            return {n: z.read(n) for n in z.namelist()}

    # -- build and verify ----------------------------------------------------------------------------
    def test_build_is_deterministic_and_complete(self):
        again = self.fresh()
        I.build(self.gen, again)
        mine, theirs = MP.tree(self.index), MP.tree(again)
        self.assertEqual(sorted(mine), sorted(theirs))
        self.assertEqual([p for p in mine if mine[p] != theirs[p]], [])
        index = json.loads(mine['index.json'])
        self.assertEqual((index['format'], index['publish']), (I.FORMAT, False))
        gen = index['profiles'][0]['generations'][0]
        info = MP.info_values(self.gen / 'base/info.txt')
        self.assertEqual((index['profiles'][0]['profile'], gen['generation']), (info['profile'], info['generation']))
        self.assertEqual(sorted(p['id'] for p in gen['packages']), sorted(d.name for d in self.gen.iterdir()))
        tree = MP.tree(self.gen)
        for pkg in gen['packages']:
            self.assertEqual((pkg['licence'], pkg['source']), (I.PLACEHOLDER, I.PLACEHOLDER))
            files = {f['path']: f for f in pkg['files']}
            mine_ = {p.split('/', 1)[1]: b for p, b in tree.items() if p.split('/')[0] == pkg['id']}
            self.assertEqual(sorted(files), sorted(mine_))
            for rel, body in mine_.items():
                self.assertEqual((files[rel]['size'], files[rel]['sha256']), (len(body), I.sha256(body)))
            self.assertEqual(pkg['content_bytes'], sum(map(len, mine_.values())))
            self.assertEqual(pkg['memory']['total'], sum(pkg['memory'][k] for k in ('code', 'data', 'state')))
            self.assertEqual(pkg['kind'], 'base' if pkg['id'] == 'base' else
                             'extension' if pkg['id'].startswith('ext-') else 'machine')
            with zipfile.ZipFile(self.index / pkg['archive']['name']) as z:
                self.assertEqual(z.namelist(), sorted(mine_, key=str.encode))
                self.assertTrue(all(i.date_time == I.STAMP and i.compress_type == zipfile.ZIP_STORED
                                    for i in z.infolist()))

    def test_verify_and_a_changed_archive(self):
        self.assertEqual(I.verify(self.index, out=quiet), len(list(self.gen.iterdir())))
        folder, index = self.copy()
        path = folder / self.package(index)['archive']['name']
        data = bytearray(path.read_bytes())
        data[40] ^= 1
        path.write_bytes(bytes(data))
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            xyf_package.main(['index', 'verify', str(folder)])
        self.assertEqual(caught.exception.code, 1)

    def test_build_refuses_an_unlisted_file(self):
        gen = self.fresh()
        shutil.copytree(self.gen, gen)
        (gen / 'alpha' / 'stray.bin').write_bytes(b'x')
        with self.assertRaises(I.Refused) as caught:
            I.build(gen, self.fresh())
        self.assertIn('stray.bin', str(caught.exception))

    # -- fetch ---------------------------------------------------------------------------------------
    def test_fetch_over_http_file_url_and_folder_is_byte_identical(self):
        want = MP.tree(self.gen)
        for where in (self.url, self.url + 'index.json', (self.index / 'index.json').as_uri(), str(self.index)):
            with self.subTest(where=where):
                target = self.fresh()
                I.fetch(where, target, out=quiet)
                got = MP.tree(target)
                self.assertEqual(sorted(got), sorted(want))
                self.assertEqual([p for p in want if got[p] != want[p]], [])
                self.assertFalse(target.with_name(target.name + '.partial').exists())

    def test_fetch_only_through_the_cli(self):
        target = self.fresh()
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            self.assertEqual(host.main(['mods', 'fetch', self.url, '--only', 'beta', '--to', str(target)]), 0)
        self.assertEqual(sorted(d.name for d in target.iterdir()), ['base', 'beta'])
        want = {p: b for p, b in MP.tree(self.gen).items() if p.split('/')[0] in ('base', 'beta')}
        self.assertEqual(MP.tree(target), want)
        S.local_set(target)                                     # what mods push checks
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            self.assertEqual(host.main(['mods', 'fetch', self.url, '--only', 'nosuch', '--to', str(self.fresh())]), 2)
        self.assertIn('not in the index: nosuch', printed.getvalue())

    # -- refusals ------------------------------------------------------------------------------------
    def test_refuses_a_bad_index(self):
        folder, index = self.copy()
        index['format'] = 'xyf-mod-index/9'
        self.save(folder, index)
        self.refused(folder, 'not a xyf-mod-index/1 index')
        (folder / 'index.json').write_text('{')
        self.refused(folder, 'not JSON')
        self.refused('ftp://example.invalid/index.json', 'unsupported URL scheme')
        self.refused(self.url + 'nosuch/', 'cannot read')

    def test_refuses_unusable_paths(self):
        cases = {'../escape': 'unusable path', '/etc/passwd': 'unusable path', 'a/../../b': 'unusable path',
                 'a//b': 'unusable path', 'a\\b': 'unusable path', 'C:/x': 'unusable path', 'x\nb': 'unusable path',
                 'n/' * 120 + 'x': 'path too long'}
        for bad, text in cases.items():
            with self.subTest(path=bad[:20]):
                folder, index = self.copy()
                self.package(index)['files'][0]['path'] = bad
                self.save(folder, index)
                self.refused(folder, text)
        folder, index = self.copy()
        self.package(index)['id'] = '../alpha'
        self.save(folder, index)
        self.refused(folder, 'bad package id')
        folder, index = self.copy()
        self.package(index)['archive']['name'] = '../../alpha.zip'
        self.save(folder, index)
        self.refused(folder, 'archive name is not')

    def test_refuses_hashes_that_do_not_match(self):
        folder, index = self.copy()
        self.package(index)['archive']['sha256'] = '0' * 64
        self.save(folder, index)
        self.refused(folder, 'archive differs from the index')
        folder, index = self.copy()                             # the archive matches, a file does not
        members = self.members(folder, index)
        members['catalog.txt'] = b'!' * len(members['catalog.txt'])
        self.rearchive(folder, index, 'alpha', members)
        self.refused(folder, 'catalog.txt: differs from the index')
        folder, index = self.copy()                             # files.sha256 inside disagrees with the index
        pkg = self.package(index)
        listed = next(f for f in pkg['files'] if f['path'] == 'catalog.txt')
        members = self.members(folder, index)
        members['catalog.txt'] = b'Z' * listed['size']
        listed['sha256'] = I.sha256(members['catalog.txt'])
        self.rearchive(folder, index, 'alpha', members)
        self.refused(folder, 'does not match the index')

    def test_refuses_members_not_listed_duplicate_compressed_or_oversize(self):
        folder, index = self.copy()
        members = self.members(folder, index)
        members['../evil'] = b'x'
        self.rearchive(folder, index, 'alpha', members)
        self.refused(folder, 'members not in the index: ../evil')
        folder, index = self.copy()
        members = self.members(folder, index)
        del members['catalog.txt']
        self.rearchive(folder, index, 'alpha', members)
        self.refused(folder, 'archive lacks catalog.txt')
        folder, index = self.copy()                             # a bomb: small archive, huge member
        bomb = next(f for f in self.package(index)['files'] if f['path'] == 'catalog.txt')
        members = self.members(folder, index)
        members['catalog.txt'] = b'\0' * (8 << 20)
        bomb.update(size=8 << 20, sha256=I.sha256(members['catalog.txt']))
        self.rearchive(folder, index, 'alpha', members, zipfile.ZIP_DEFLATED)
        self.refused(folder, 'compressed member')
        folder, index = self.copy()
        self.package(index)['files'][0]['size'] = I.MAX_FILE + 1
        self.save(folder, index)
        self.refused(folder, 'oversize size')
        folder, index = self.copy()
        self.package(index)['archive']['size'] = I.MAX_ARCHIVE + 1
        self.save(folder, index)
        self.refused(folder, 'oversize archive')
        folder, index = self.copy()                             # the server sends more than the index says
        pkg = self.package(index)
        pkg['archive']['size'] -= 1
        self.save(folder, index)
        self.refused(folder, 'larger than')
        folder, index = self.copy()                             # a member's header lies about its size
        pkg = self.package(index)
        f = next(f for f in pkg['files'] if f['path'] == 'catalog.txt')
        f['size'] += 1
        self.save(folder, index)
        self.refused(folder, 'member size')

    def test_refuses_a_non_empty_target_and_an_ambiguous_generation(self):
        target = self.fresh()
        target.mkdir()
        (target / 'x').write_bytes(b'x')
        with self.assertRaises(I.Refused) as caught:
            I.fetch(self.index, target, out=quiet)
        self.assertIn('not empty', str(caught.exception))
        folder, index = self.copy()
        twin = json.loads(json.dumps(index['profiles'][0]['generations'][0]))
        twin['generation'] = 'other-1'
        for pkg in twin['packages']:
            pkg['archive']['name'] = pkg['archive']['name'].replace(index['profiles'][0]['generations'][0]['generation'],
                                                                    'other-1')
        index['profiles'][0]['generations'].append(twin)
        self.save(folder, index)
        self.refused(folder, 'choose one generation')
        self.refused(folder, 'choose one generation', generation='nosuch')
        target = self.fresh()
        I.fetch(folder, target, generation=index['profiles'][0]['generations'][0]['generation'], out=quiet)
        self.assertEqual(MP.tree(target), MP.tree(self.gen))

    # -- the fetched set goes to the simulator ---------------------------------------------------------
    @unittest.skipIf(SIM_UNAVAILABLE, SIM_UNAVAILABLE)
    def test_fetched_set_pushed_to_the_simulator_is_identical(self):
        target = self.fresh()
        I.fetch(self.url, target, out=quiet)
        state = Path(tempfile.mkdtemp(prefix='sim-', dir=self.work))
        shutil.copytree(sim.seed(state / 'seed'), state / 'yaffs2')
        simulator = sim.Simulator(state / 'yaffs2', state / 'bootpart.bin', build_dir=sim.build(),
                                  log=state / 'log.txt').start()
        self.addCleanup(simulator.stop)
        saved = host.SIM
        self.addCleanup(setattr, host, 'SIM', saved)
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(host.main(['--sim', simulator.address, 'mods', 'push', str(target), '--verify']), 0)
        modstore.copy_set(self.gen, state / 'want')                    # the flat store a push leaves (#124)
        want = MP.tree(state / 'want')
        self.assertIn('verify: %d files read back identical' % (len(want) - 1), printed.getvalue())
        info = MP.info_values(self.gen / 'base/info.txt')
        stored = MP.tree(state / 'yaffs2/xy-forever/mods' / info['generation'])
        self.assertEqual(sorted(stored), sorted(want))
        self.assertEqual([p for p in want if stored[p] != want[p]], [])


if __name__ == '__main__':
    unittest.main()
