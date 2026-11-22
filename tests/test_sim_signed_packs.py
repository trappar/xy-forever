"""Exercise signed inbox admission through the menu and serial service."""

import contextlib
import hashlib
import io
import json
import random
import struct
import tempfile
import time
import unittest
from pathlib import Path

import pytest
import test_simulator as S
from test_native_package import package
from test_tfw import KEY, container
from test_tfw_os import PUBLIC_KEY, signed

from xy_forever import bounds, package_store, sim
from xy_forever import pack_change as packs
from xy_forever.usb_proto import ids

UNAVAILABLE = sim.available() or (None if sim.STOCK_OS.is_file() else 'private stock os.ldr absent')
PREFIX = 'firmware/blackfin/content/'
OTP = bytes((9, 56)) + PUBLIC_KEY + bytes((3, 16)) + KEY[:16] + bytes((4, 16)) + KEY[16:]  # the keys A and B read
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


@pytest.mark.sim
@unittest.skipIf(UNAVAILABLE, UNAVAILABLE)
class SignedPackImport(unittest.TestCase):
    connect = S.SimulatorTest.connect
    wait = S.SimulatorTest.wait

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='xyf-signed-pack-')
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name)
        self.root = sim.seed(path / 'yaffs2')
        self.inbox = self.root / 'xy-forever/inbox'
        self.inbox.mkdir(exist_ok=True)
        self.data = fixture()
        (self.inbox / 'test.tfw').write_bytes(self.data)
        self.sim = machine = sim.Simulator(self.root, path / 'bootpart.bin', otp=OTP, log=path / 'log.txt').start()
        self.addCleanup(machine.stop)
        self.links = []
        self.addCleanup(lambda: [link.t.close() for link in self.links])
        self.dev = self.connect()
        for folder, name in (('/yaffs2', b'xy-forever'), ('/yaffs2/xy-forever', b'inbox'), (packs.INBOX, b'test.tfw')):
            code, entries = self.dev.list_dir(folder)
            self.assertEqual(code, 0, folder)
            self.assertIn(
                (0 if name.endswith(b'.tfw') else 1, name), [(kind, raw) for kind, _, raw in entries], (folder, entries)
            )
        self.retained = self.root / 'xy-forever/packs/fixture'
        self.live = self.root / 'content/dlc/fixture'
        self.records = self.root / 'xy-forever/pack-records/fixture'

    def call(self, op, name='', version=1):
        return self.dev.call(ids.PACKS_MANAGE, struct.pack('<II', version, op) + name.encode() + b'\0')

    def settled(self, op, name):
        """packs.manage op, sent again while it answers busy (a write window settling); its status."""
        hang_deadline = time.monotonic() + bounds.WAIT_S
        while (code := self.call(op, name)[0]) == ids.S_BUSY and time.monotonic() < hang_deadline:
            self.sim.advance(20)
        return code

    def listed(self, pack_id):
        """packs list's rows for pack_id."""
        out = io.StringIO()
        with contextlib.redirect_stdout(out):  # right after a toggle: its window close is still out (busy)
            self.assertEqual(packs.list_packs(self.dev), 0, out.getvalue())
        return [row for row in json.loads(out.getvalue()) if row['id'] == pack_id]

    def start(self, name='test.tfw'):
        """packs.manage import, sent again while it answers busy (the write window settling)."""
        hang_deadline = time.monotonic() + bounds.WAIT_S
        while True:
            code, body = self.call(packs.OP_IMPORT, name)
            if code != ids.S_BUSY or time.monotonic() >= hang_deadline:
                return code, body
            self.sim.advance(20)

    def finish(self):
        hang_deadline = time.monotonic() + bounds.WAIT_S
        while time.monotonic() < hang_deadline:
            code, body = self.call(packs.OP_STATUS)
            self.assertEqual(code, ids.S_OK)
            phase, done, total, error, self.site, self.detail, self.traced = struct.unpack('<7I', body)
            if phase in (3, 4):
                return phase, done, total, error
            self.sim.advance(20)
        self.fail('signed import did not finish')

    def test_signed_import_retains_one_tree_then_enables_and_disables(self):
        """Kept: #195's import end to end: one retained tree of hardlinked blobs, its records beside it, listed; then
        enable (a verified signed pack is trusted, listed as not tested on this firmware) and disable; then packs
        forget deletes it and its blobs, so the same pack imports again."""
        catalog = self.root / 'content/op1_factory.json'
        original = catalog.read_bytes()
        self.assertEqual(self.start()[0], ids.S_OK)
        self.assertEqual(self.finish(), (3, 2, 2, ids.S_OK))
        self.assertEqual(self.traced, 0)  # nothing failed: no log kept
        listed = self.listed('fixture')
        untested = dict(id='fixture', signed=True, firmware='not tested on this firmware')
        self.assertEqual(listed, [dict(untested, state='retained')])
        sample = self.retained / 'samples/hand.wav'
        self.assertEqual(sample.read_bytes(), SAMPLE)
        digest = hashlib.sha256(SAMPLE).hexdigest()
        blob = self.root / 'xy-forever/blobs' / digest
        self.assertEqual(sample.stat().st_ino, blob.stat().st_ino)
        self.assertEqual(sorted(p.name for p in self.retained.iterdir()), ['manifest.json', 'samples'])  # no records
        refs = (self.records / 'xyf-pack-v1.sha256').read_text().splitlines()
        self.assertEqual([line[66:] for line in refs], ['manifest.json', 'samples/hand.wav'])
        self.assertEqual(refs[1], digest + '  samples/hand.wav')
        self.assertEqual(sample.stat().st_mode & 0o777, 0o444)
        with package_store.Session(self.dev, sweep=False) as session:
            self.assertIn(digest, session.references())
            self.assertEqual(session.collect(), 0)
        record = (self.records / 'xyf-pack.txt').read_text()
        self.assertEqual(record, 'version=1\nsha256=' + hashlib.sha256(self.data[0x380:]).hexdigest() + '\n')
        self.assertEqual(self.settled(packs.OP_ENABLE, 'fixture'), ids.S_OK)
        self.assertEqual(self.listed('fixture'), [dict(untested, state='active')])
        self.assertEqual(sorted(p.name for p in self.live.iterdir()), ['manifest.json', 'samples'])
        self.assertFalse(self.retained.exists())
        self.assertEqual(self.settled(packs.OP_DISABLE, 'fixture'), ids.S_OK)
        self.assertEqual(self.listed('fixture'), [dict(untested, state='retained')])
        self.assertFalse(self.live.exists())
        self.assertEqual(self.start()[0], ids.S_OK)
        self.assertEqual(self.finish()[-1], ids.S_STATE)
        self.assertEqual(catalog.read_bytes(), original)
        self.assertEqual((self.inbox / 'test.tfw').read_bytes(), self.data)
        with package_store.Session(self.dev, sweep=False) as session:
            self.assertEqual(packs.forget(session, 'fixture'), 2)  # the manifest's blob and the sample's
        self.assertFalse(self.retained.exists())
        self.assertFalse(self.records.exists())
        self.assertFalse(blob.exists())
        self.assertEqual(self.start()[0], ids.S_OK)
        self.assertEqual(self.finish()[-1], ids.S_OK)
        self.assertEqual(sample.read_bytes(), SAMPLE)

    def test_corrupt_source_never_publishes(self):
        """Kept: a request or archive that fails authentication publishes nothing."""
        self.assertEqual(self.call(packs.OP_STATUS, version=2)[0], ids.S_UNSUPPORTED)
        self.assertEqual(self.call(packs.OP_IMPORT, '../test.tfw')[0], ids.S_BAD_REQUEST)
        self.assertEqual(self.start('missing.tfw')[0], ids.S_NOT_FOUND)
        data = self.data[:-32] + bytes(32)
        (self.inbox / 'bad.tfw').write_bytes(data)
        self.assertEqual(self.start('bad.tfw')[0], ids.S_OK)
        self.assertEqual(self.finish()[-1], ids.S_SHA)
        self.assertFalse(self.retained.exists())
        self.assertFalse(self.live.exists())

    def test_failed_member_write_reports_its_site_and_removes_scratch(self):
        """Kept: a failed member write names its step and member (the device's storage error 7) and removes the scratch."""
        self.sim.set('fail_write', 1)  # A's first data write: EIO
        self.assertEqual(self.start()[0], ids.S_OK)
        self.assertEqual(self.finish()[-1], ids.S_STORAGE)
        self.assertEqual(self.site & 0xFFFF, 3 << 8 | 3)  # extract step, the member's write
        self.assertEqual(self.site & 0x30000, 0)  # the source closed, the link held
        self.assertTrue(self.site & 0x40000)  # so the scratch was removed
        self.assertEqual(self.detail, 1 << 16 | 0x4000 | ids.S_STORAGE)  # member 1 (manifest.json), A's status
        self.assertIn('write, member 1', packs.failure_site(self.site, self.detail))
        self.assertFalse(self.retained.exists())
        self.assertEqual(list((self.root / 'xy-forever/tmp').glob('pkg-v1-*')), [])

    def test_preflight_refuses_insufficient_space_before_writes(self):
        """Kept: the free-space preflight refuses before any write."""
        self.sim.set('statfs_block_bytes', 4096)
        self.sim.set('statfs_free_blocks', 1)
        self.sim.set('statfs_override', 1)
        self.assertEqual(self.start()[0], ids.S_OK)
        self.assertEqual(self.finish()[-1], ids.S_TOO_LARGE)
        self.assertFalse(self.retained.parent.exists())
        self.assertFalse(self.live.exists())
        self.assertFalse((self.root / 'xy-forever/blobs').exists())

    def test_seed_cut_leaves_no_partial_pack_and_retry_recovers(self):
        """Kept: a power cut mid-import leaves no partial pack, and a retry completes."""
        self.sim.set('rename_fault_kind', 2)
        self.sim.set('rename_fault', 1)
        before = self.sim.status()['cuts']
        self.assertEqual(self.start()[0], ids.S_OK)
        self.wait(lambda s: s['cuts'] == before + 1 and s['listening'], what='the restart after the cut rename')
        self.assertFalse(self.retained.exists())
        self.assertFalse(self.live.exists())
        self.dev = self.connect(startup_ready=True)
        # The menu's own window after the cut closes before the retry (an import that settles it halts A here).
        self.wait(lambda s: not s['rw_open'], what="the menu's startup window closed")
        self.assertEqual(self.start()[0], ids.S_OK)
        self.assertEqual(self.finish()[-1], ids.S_OK)
        sample = self.retained / 'samples/hand.wav'
        blob = self.root / 'xy-forever/blobs' / hashlib.sha256(SAMPLE).hexdigest()
        self.assertEqual(sample.stat().st_ino, blob.stat().st_ino)

    def test_failed_member_open_reports_bs_log(self):
        """Kept: #195's device diagnostic, a cheap permanent aid. A failed import keeps B's log of its last requests to
        A (nothing asked of A), and packs.manage operation 5 returns it: here the source authenticated, then the
        member's sealed file that A failed to create, its folders (mkdir -p), the create again, then the source's
        close."""
        self.sim.set('fail_write_open', 2)  # the member's sealed file: A's create fails, and again after mkdir -p
        self.assertEqual(self.start()[0], ids.S_OK)
        self.assertEqual(self.finish()[-1], ids.S_STORAGE)
        self.assertEqual(self.site & 0xFFFF, 3 << 8 | 1)  # extract step, the member's open
        self.assertEqual(self.detail, 1 << 16 | 0x4000 | ids.S_STORAGE)  # member 1 (manifest.json), A's status
        self.assertEqual(self.traced, 32)  # the log is full
        log = packs.b_log_lines(packs.signed_trace(self.dev))
        self.assertEqual(len(log), 32)
        authenticated = next(i for i, line in enumerate(log) if 'mark 0x0001' in line)
        after = log[authenticated + 1 :]
        self.assertRegex(after[0], r'op 0x21 blob create 0x4203 -> 7 errno 0, \d+ ms$')
        self.assertTrue(all('mkdir' in line for line in after[1:-2]), after)
        self.assertRegex(after[-2], r'op 0x21 blob create 0x4203 -> 7 errno 0, \d+ ms$')
        self.assertRegex(after[-1], r'op 0x2d close 0x\w+ -> 0 errno 0, \d+ ms$')
        self.assertFalse(self.retained.exists())

    def test_requests_per_mib_of_members_stay_bounded(self):
        """Kept: #195's speed. Each 4 KiB of member data costs one source read and one write to A, and nothing else:
        each member goes straight into its sealed blob file (no copy on A, no read-back). The old seal path took about
        1,150 requests per MiB; this bound fails it."""
        mib = 4
        noise = random.Random(5).randbytes(mib << 19)  # random bytes: stored as they are, so reads match writes
        data = signed(
            container(
                package(
                    [
                        (PREFIX + 'partial_upgrade', b''),
                        (PREFIX + 'dlc/fixture/manifest.json', b'{"folder":"fixture"}'),
                        (PREFIX + 'dlc/fixture/samples/a.wav', noise),
                        (PREFIX + 'dlc/fixture/samples/b.wav', noise[::-1]),
                    ]
                )
            )
        )
        (self.inbox / 'big.tfw').write_bytes(data)
        before = self.sim.status()['to_a']
        self.assertEqual(self.start('big.tfw')[0], ids.S_OK)
        self.assertEqual(self.finish()[-1], ids.S_OK)
        requests = self.sim.status()['to_a'] - before
        self.assertLessEqual(requests, 600 * mib, requests)  # 2 x 256 per MiB, plus the fixed steps
        self.assertEqual((self.retained / 'samples/b.wav').read_bytes(), noise[::-1])
