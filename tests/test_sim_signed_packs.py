"""Exercise signed inbox admission through the menu and serial service."""

import hashlib
import struct
import tempfile
import time
import unittest
from pathlib import Path

from test_native_package import package
from test_tfw import KEY, container
from test_tfw_os import PUBLIC_KEY, signed

import xyf_host as host
from xy_forever import pack_change as packs
from xy_forever import package_store, sim
from xy_forever.usb_proto import ids

UNAVAILABLE = sim.available() or (None if sim.STOCK_OS.is_file() else 'private stock os.ldr absent')
PREFIX = 'firmware/blackfin/content/'
SAMPLE = b'RIFF' + bytes(range(128))


def fixture():
    return signed(
        container(
            package(
                [
                    (PREFIX + 'partial_upgrade', b''),
                    (PREFIX + 'dlc/fixture/manifest.json', b'{"folder":"fixture"}'),
                    (PREFIX + 'dlc/fixture/samples/hand.wav', SAMPLE),
                ]
            )
        )
    )


@unittest.skipIf(UNAVAILABLE, UNAVAILABLE)
class SignedPackImport(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='xyf-signed-pack-')
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name)
        self.root = sim.seed(path / 'yaffs2')
        self.inbox = self.root / 'xy-forever/inbox'
        self.inbox.mkdir(exist_ok=True)
        self.data = fixture()
        (self.inbox / 'test.tfw').write_bytes(self.data)
        otp = bytes((9, 56)) + PUBLIC_KEY + bytes((3, 16)) + KEY[:16] + bytes((4, 16)) + KEY[16:]
        self.machine = machine = sim.Simulator(self.root, path / 'bootpart.bin', otp=otp, log=path / 'log.txt').start()
        self.addCleanup(machine.stop)
        self.dev = host.Device(host.TcpPort(machine.address), on_log=None)
        self.addCleanup(self.dev.t.close)
        self.assertEqual(self.dev.hello()[0], 0)
        for folder, name in (('/yaffs2', b'xy-forever'), ('/yaffs2/xy-forever', b'inbox'), (packs.INBOX, b'test.tfw')):
            code, entries = self.dev.list_dir(folder)
            self.assertEqual(code, 0, folder)
            self.assertIn(
                (0 if name.endswith(b'.tfw') else 1, name), [(kind, raw) for kind, _, raw in entries], (folder, entries)
            )
        self.retained = self.root / 'xy-forever/packs/fixture'
        self.live = self.root / 'content/dlc/fixture'

    def call(self, op, name='', version=1):
        return self.dev.call(ids.PACKS_MANAGE, struct.pack('<II', version, op) + name.encode() + b'\0')

    def start(self, name='test.tfw'):
        deadline = time.monotonic() + 15
        while True:
            code, body = self.call(packs.OP_IMPORT, name)
            if code != ids.S_BUSY or time.monotonic() >= deadline:
                return code, body
            time.sleep(0.02)

    def finish(self):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            code, body = self.call(packs.OP_STATUS)
            self.assertEqual(code, ids.S_OK)
            phase, done, total, error = struct.unpack('<IIII', body)
            if phase in (3, 4):
                return phase, done, total, error
            time.sleep(0.02)
        self.fail('signed import did not finish')

    def test_signed_import_retains_one_tree_and_refuses_unknown_compatibility(self):
        catalog = self.root / 'content/op1_factory.json'
        original = catalog.read_bytes()
        self.assertEqual(self.start()[0], ids.S_OK)
        self.assertEqual(self.finish(), (3, 2, 2, ids.S_OK))
        sample = self.retained / 'samples/hand.wav'
        self.assertEqual(sample.read_bytes(), SAMPLE)
        digest = hashlib.sha256(SAMPLE).hexdigest()
        blob = self.root / 'xy-forever/blobs' / digest
        self.assertEqual(sample.stat().st_ino, blob.stat().st_ino)
        self.assertEqual(sample.stat().st_mode & 0o777, 0o444)
        with package_store.Session(self.dev, sweep=False) as session:
            self.assertIn(digest, session.references())
            self.assertEqual(session.collect(), 0)
        record = (self.retained / 'xyf-pack.txt').read_text()
        self.assertEqual(record, 'version=1\nsha256=' + hashlib.sha256(self.data[0x380:]).hexdigest() + '\n')
        self.assertEqual(self.call(packs.OP_ENABLE, 'fixture')[0], ids.S_REFUSED)
        self.assertFalse(self.live.exists())
        self.assertEqual(self.start()[0], ids.S_OK)
        self.assertEqual(self.finish()[-1], ids.S_STATE)
        self.assertEqual(catalog.read_bytes(), original)
        self.assertEqual((self.inbox / 'test.tfw').read_bytes(), self.data)

    def test_corrupt_source_never_publishes(self):
        self.assertEqual(self.call(packs.OP_STATUS, version=2)[0], ids.S_UNSUPPORTED)
        self.assertEqual(self.call(packs.OP_IMPORT, '../test.tfw')[0], ids.S_BAD_REQUEST)
        self.assertEqual(self.start('missing.tfw')[0], ids.S_NOT_FOUND)
        data = self.data[:-32] + bytes(32)
        (self.inbox / 'bad.tfw').write_bytes(data)
        self.assertEqual(self.start('bad.tfw')[0], ids.S_OK)
        self.assertEqual(self.finish()[-1], ids.S_SHA)
        self.assertFalse(self.retained.exists())
        self.assertFalse(self.live.exists())

    def test_preflight_refuses_insufficient_space_before_writes(self):
        self.machine.set('statfs_block_bytes', 4096)
        self.machine.set('statfs_free_blocks', 1)
        self.machine.set('statfs_override', 1)
        self.assertEqual(self.start()[0], ids.S_OK)
        self.assertEqual(self.finish()[-1], ids.S_TOO_LARGE)
        self.assertFalse(self.retained.parent.exists())
        self.assertFalse(self.live.exists())
        self.assertFalse((self.root / 'xy-forever/blobs').exists())

    def test_seed_cut_leaves_no_partial_pack_and_retry_recovers(self):
        self.machine.set('rename_fault_kind', 2)
        self.machine.set('rename_fault', 1)
        before = self.machine.status()['cuts']
        self.assertEqual(self.start()[0], ids.S_OK)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            status = self.machine.status()
            if status['cuts'] == before + 1 and status['listening']:
                break
            time.sleep(0.02)
        else:
            self.fail('simulator did not restart after the rename')
        self.assertFalse(self.retained.exists())
        self.assertFalse(self.live.exists())
        self.dev.t.close()
        self.dev = host.Device(host.TcpPort(self.machine.address), on_log=None)
        self.addCleanup(self.dev.t.close)
        self.assertEqual(self.dev.hello()[0], 0)
        self.assertEqual(self.start()[0], ids.S_OK)
        self.assertEqual(self.finish()[-1], ids.S_OK)
        sample = self.retained / 'samples/hand.wav'
        blob = self.root / 'xy-forever/blobs' / hashlib.sha256(SAMPLE).hexdigest()
        self.assertEqual(sample.stat().st_ino, blob.stat().st_ino)
