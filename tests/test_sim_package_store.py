"""Package transactions on the real simulator service, with a cut at every mutation boundary."""
import hashlib
from pathlib import Path
import shutil
import struct
import tempfile
import time
import unittest

import xyf_host as host
from xy_forever import package_store as S, sim, usb_proto as P, xyf_entry
from test_ldr import image
from scripts.package_pass import exercise


def package(label, content):
    data = image([(4, 0x1000, 2, 0, label.encode())])
    files = {'os.ldr': data, 'info.txt': xyf_entry.info_text(label, '0', 'custom', data).encode(),
             'content.sha256': S.list_text(content), 'firmware.version': b'test-v1\n'}
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
        super().write(*args); self.after()

    def change(self, *args):
        super().change(*args); self.after()

    def rename(self, *args):
        super().rename(*args); self.after()


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
        self.sim = sim.Simulator(self.root, self.dir / 'boot.bin', build_dir=self.build, log=self.dir / 'sim.log').start()
        self.dev = host.Device(host.TcpPort(self.sim.address), on_log=None)
        self.assertEqual(self.dev.hello()[0], 0)
        self.addCleanup(self.close)

    def close(self):
        self.dev.t.close(); self.sim.stop(); shutil.rmtree(self.dir)

    def reconnect(self):
        self.dev.t.close()
        until = time.monotonic() + 15
        while not self.sim.status()['listening']:
            self.assertLess(time.monotonic(), until); time.sleep(0.02)
        self.dev = host.Device(host.TcpPort(self.sim.address), on_log=None)
        self.assertEqual(self.dev.hello()[0], 0)

    def test_device_pass_proved_in_simulator(self):
        self.assertEqual(len(exercise(self.dev, 'opxy-121')), 2)

    def test_session_versions_exclusion_and_abandoned_owner(self):
        token = b'a' * 32
        request = lambda version, action, value=token: self.dev.call(P.ids.PACKAGE_SESSION,
            struct.pack('<II', version, action) + value)[0]
        self.assertEqual(request(2, 0), P.ids.S_UNSUPPORTED)
        self.assertEqual(request(2, 99), P.ids.S_UNSUPPORTED)
        self.assertEqual(request(1, 0), 0)
        self.assertEqual(self.dev.call(P.ids.MODS_APPLY, b'')[0], P.ids.S_BUSY)
        self.assertEqual(self.dev.call(P.REBOOT)[0], P.ids.S_BUSY)
        self.assertEqual(request(1, 2, b'b' * 32), P.ids.S_STATE)
        self.sim.advance(9000)
        self.assertEqual(request(1, 0, b'b' * 32), P.ids.S_BUSY)
        self.sim.advance(1100)
        until = time.monotonic() + 5
        while request(1, 0, b'b' * 32) == P.ids.S_BUSY:
            self.assertLess(time.monotonic(), until); time.sleep(0.02)
        self.assertEqual(request(1, 1), P.ids.S_STATE)
        self.assertEqual(request(1, 2, b'b' * 32), 0)

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
                session.stop = stop; session.simulator = self.sim
                if remove:
                    session.remove(target)
                else:
                    session.retain(xyf_entry.read_files_sha256(new['content.sha256']), new_sources)
                    session.publish(target, new)
                session.collect()
                return session.steps

        for remove in (False, True):
            prepare(); steps = run(remove=remove)
            self.assertGreater(steps, 3)
            for boundary in range(1, steps + 1):
                with self.subTest(remove=remove, boundary=boundary):
                    prepare()
                    with self.assertRaises(Cut):
                        run(boundary, remove)
                    self.reconnect()
                    entry = self.root / 'xy-forever/firmware/package-cut'
                    if entry.exists():
                        actual = {p.relative_to(entry).as_posix(): p.read_bytes() for p in entry.rglob('*') if p.is_file()}
                        self.assertIn(actual, (old, new))
                    self.assertEqual((self.root / 'xy-forever/blobs' / hashlib.sha256(b'shared').hexdigest()).read_bytes(), b'shared')
                    self.assertEqual((self.root / 'xy-forever/state-a.txt').read_bytes(), state)
                    with S.Session(self.dev) as session:
                        session.collect()
                    until = time.monotonic() + 10
                    while list((self.root / 'xy-forever/tmp').glob('pkg-v1-*')):
                        self.assertLess(time.monotonic(), until); time.sleep(0.02)
