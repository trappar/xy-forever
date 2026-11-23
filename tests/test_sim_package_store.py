"""Package transactions on the real simulator service, with a cut at every mutation boundary."""

import hashlib
import shutil
import tempfile
import time
import unittest
from pathlib import Path

import pytest
from test_ldr import image

from xy_forever import package_store as S
from xy_forever import sim, xyf_entry
from xy_forever import xyf_host as host


def package(label, content):
    data = image([(4, 0x1000, 2, 0, label.encode())])
    files = {
        'os.ldr': data,
        'info.txt': xyf_entry.info_text(label, '0', 'custom', data).encode(),
        'content.sha256': S.list_text(content),
        'firmware.version': b'test-v1\n',
    }
    files['files.sha256'] = S.list_text(files)
    S.validate(files)
    return files


class Cut(Exception):
    pass


class CountSession(S.Session):
    steps = 0
    stop = None
    simulator = None

    def after(self):
        self.steps += 1
        if self.steps == self.stop:
            self.simulator.control('cut')
            raise Cut()

    def write(self, *args):
        super().write(*args)
        self.after()

    def change(self, *args):
        super().change(*args)
        self.after()

    def rename(self, *args):
        super().rename(*args)
        self.after()


@unittest.skipIf(sim.available(), sim.available())
class Packages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='xyf-sim-package-')
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.work = Path(cls.tmp.name)
        cls.build = sim.build()

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(dir=self.work))
        self.root = sim.seed(self.dir / 'yaffs2', image([(4, 0x1000, 2, 0, b'base')]))
        self.sim = sim.Simulator(self.root, self.dir / 'boot.bin', binary=self.build, log=self.dir / 'sim.log').start()
        self.dev = host.Device(host.TcpPort(self.sim.address), on_log=None)
        self.assertEqual(self.dev.hello()[0], 0)
        self.addCleanup(self.close)

    def close(self):
        self.dev.t.close()
        self.sim.stop()
        shutil.rmtree(self.dir)

    def reconnect(self):
        self.dev.t.close()
        until = time.monotonic() + 15
        while not self.sim.status()['listening']:
            self.assertLess(time.monotonic(), until)
            self.sim.advance(20)
        self.dev = host.Device(host.TcpPort(self.sim.address), on_log=None)
        self.assertEqual(self.dev.hello()[0], 0)

    @pytest.mark.sim
    def test_factory_retention_reuses_live_files_without_reading_blobs(self):
        sources = {'factory.bin': b'factory bytes', 'also-mod.bin': b'shared bytes', 'absent.bin': b'cached'}
        references = xyf_entry.read_files_sha256(S.list_text(sources))
        blobs = self.root / 'xy-forever/blobs'
        blobs.mkdir(exist_ok=True)
        (self.root / 'content').mkdir(exist_ok=True)
        for sha, rel in references:
            (blobs / sha).write_bytes(sources[rel])
            if rel != 'absent.bin':
                (self.root / 'content' / rel).write_bytes(sources[rel])
        mod = self.root / 'xy-forever/firmware/mods-retained'
        mod.mkdir()
        (mod / 'files.sha256').write_bytes(S.list_text({'content/shared.bin': sources['also-mod.bin']}))

        class Retain(S.Session):
            def read(self, path, optional=False):
                assert not path.startswith(S.BLOBS + '/'), path
                return super().read(path, optional)

        with Retain(self.dev) as session:
            session.retain(references, sources, factory=True)
        for sha, rel in references:
            self.assertEqual((blobs / sha).exists(), rel != 'factory.bin')
            if rel != 'absent.bin':
                self.assertEqual((self.root / 'content' / rel).read_bytes(), sources[rel])

    @pytest.mark.sweep  # parked: a serial chain of simulator restarts, the run's critical path
    def test_cut_between_every_add_remove_and_gc_step(self):
        target = S.LIBRARY + '/package-cut'
        kept = S.LIBRARY + '/package-kept'
        shared = {'display/shared.svg': b'shared'}
        old_sources = dict(shared, **{'display/old.svg': b'old'})
        new_sources = dict(shared, **{'display/new.svg': b'new'})
        old, new, other = package('old', old_sources), package('new', new_sources), package('kept', shared)
        state = (self.root / 'xy-forever/state-a.txt').read_bytes()

        def prepare():
            with S.Session(self.dev) as session:
                for name, body, sources in ((target, old, old_sources), (kept, other, shared)):
                    session.retain(xyf_entry.read_files_sha256(body['content.sha256']), sources)
                    session.publish(name, body)
                session.collect()

        def run(stop=None, remove=False):
            with CountSession(self.dev) as session:
                session.stop = stop
                session.simulator = self.sim
                if remove:
                    session.remove(target)
                else:
                    session.retain(xyf_entry.read_files_sha256(new['content.sha256']), new_sources)
                    session.publish(target, new)
                session.collect()
                return session.steps

        for remove in (False, True):
            prepare()
            steps = run(remove=remove)
            self.assertGreater(steps, 3)
            for boundary in range(1, steps + 1):
                with self.subTest(remove=remove, boundary=boundary):
                    prepare()
                    with self.assertRaises(Cut):
                        run(boundary, remove)
                    self.reconnect()
                    entry = self.root / 'xy-forever/firmware/package-cut'
                    if entry.exists():
                        actual = {
                            p.relative_to(entry).as_posix(): p.read_bytes() for p in entry.rglob('*') if p.is_file()
                        }
                        self.assertIn(actual, (old, new))
                    self.assertEqual(
                        (self.root / 'xy-forever/blobs' / hashlib.sha256(b'shared').hexdigest()).read_bytes(), b'shared'
                    )
                    self.assertEqual((self.root / 'xy-forever/state-a.txt').read_bytes(), state)
                    with S.Session(self.dev) as session:
                        session.collect()
                    until = time.monotonic() + 10
                    while list((self.root / 'xy-forever/tmp').glob('pkg-v1-*')):
                        self.assertLess(time.monotonic(), until)
                        self.sim.advance(20)
