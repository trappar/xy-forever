"""The device simulator (xy_forever/sim.py, sim/): the real B service, A storage server, file
boot and peer session, built for the Mac, driven over TCP by xyf_host.py's own client.
Covers every command the product build answers, the HELLO, a device pass and each fault hook.
Needs clang++ and the private stock 1.2.1 images (skipped without them). No device access."""

import contextlib
import gc
import hashlib
import io
import json
import shutil
import socket
import struct
import tempfile
import threading
import time
import unittest
import warnings
import zlib
from pathlib import Path

import pytest

from xy_forever import bounds, census, release, sim, xyf_build
from xy_forever import usb_proto as P
from xy_forever import usb_proto_ids as ids
from xy_forever import xyf_host as host
from xy_forever.startup_ready import wait_startup_ready

ROOT = Path(__file__).resolve().parents[1]

UNAVAILABLE = sim.available() or (None if sim.STOCK_OS.is_file() else 'private stock 1.2.1 os.ldr absent')
BASE = '/yaffs2/xy-forever/'
PRE_V1 = 'XYF1;service=usb;role=b;proto=1;install=stock-88a06a7e'
SPEC = json.loads((ROOT / 'protocol' / 'v1.json').read_text())


PARTITION_CLASS, PATH_CLASS, SECTOR_CLASS, WRITE_CLASS = 0x11907694, 0x119076B4, 0x11907348, 0x11907710
EACCES, EBADF, EROFS, EBUSY, EMFILE = 13, 9, 30, 16, 24
COMMAND_TAG = 0x434D  # peer_services.hpp: a command item's argument tag
INSTALL_ITEM, USB_MODE_ITEM, FILE_WRITE_ITEM = 4, 7, 10  # storage_server.hpp
MARKER_SPELLINGS = [
    '/yaffs2/bootloader.dev',
    '/yaffs2//bootloader.dev',
    '/yaffs2/bootloader.dev/',
    '/yaffs2/BOOTLOADER.DEV',
    '/yaffs2/Bootloader.Dev//',
]
ROOT_SPELLINGS = ['/yaffs2', '/yaffs2/', '/yaffs2//']
RAW_LAYER = [
    (PARTITION_CLASS, 0x18, 1, b''),
    (PARTITION_CLASS, 0x14, 0, b''),
    (PARTITION_CLASS, 4, 0, b''),
    (SECTOR_CLASS, 0x11, 0, b''),
    (SECTOR_CLASS, 0x12, 1, b''),
    (WRITE_CLASS, 0x15, 0, bytes(512)),
    (WRITE_CLASS, 0x16, 1, bytes(512)),
]
# F1: the stock bootloader's boot read (b restarted alone): outside install mode a never answers
# these; it holds the request, closes storage and restarts. The rest of the raw layer is refused.
BOOT_REQUESTS = [
    (c, o, a, b)
    for c, o, a, b in RAW_LAYER
    if (c, o) in ((PARTITION_CLASS, 0x18), (SECTOR_CLASS, 0x11), (SECTOR_CLASS, 0x12))
]
REFUSED_RAW = [r for r in RAW_LAYER if r not in BOOT_REQUESTS]


def raw_path(path):
    return path.encode('utf-8') + b'\0'


@unittest.skipIf(UNAVAILABLE, UNAVAILABLE)
class SimulatorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='xyf-sim-')
        cls.out = sim.build()  # the default variant `uv sync` built
        cls.seeded = sim.seed(Path(cls.tmp.name) / 'seed')
        cls.runs = 0

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        type(self).runs += 1
        self.dir = Path(self.tmp.name) / ('run-%d' % self.runs)
        shutil.copytree(self.seeded, self.dir / 'yaffs2')
        self.sim = sim.Simulator(
            self.dir / 'yaffs2', self.dir / 'bootpart.bin', binary=self.out, log=self.dir / 'log.txt'
        ).start()
        self.links = []

    def tearDown(self):
        for link in self.links:
            link.t.close()
        self.sim.stop()

    # -- helpers ---------------------------------------------------------------------------
    def connect(self, hello=True, startup_ready=False):
        # Opt in before isolated mutation/counter setup; cold boot and fault observations stay raw.
        dev = host.Device(host.TcpPort(self.sim.address), on_log=None)
        self.links.append(dev)
        if hello:
            code, text = dev.hello()
            self.assertEqual(code, 0)
            if startup_ready:
                wait_startup_ready(
                    text, lambda remaining: dev.call(ids.STORAGE_SPACE, struct.pack('<I', 1), timeout=remaining)[0]
                )
        return dev

    def file(self, path):
        return (self.dir / 'yaffs2' / path[len('/yaffs2/') :]).read_bytes()

    def wait(self, predicate, what='simulator state'):
        """Until predicate(status), the board clock advanced between tries: a processor's restart delay
        (its bootloader's start time) is passed with advance, never waited for. The wall-clock deadline
        (bounds.WAIT_S) is a hang bound only, never a budget: the test's CPU budget (tests/conftest.py)
        holds its time."""
        hang_deadline = time.monotonic() + bounds.WAIT_S
        while time.monotonic() < hang_deadline:
            status = self.sim.status()
            if predicate(status):
                return status
            if 'resetting' in (status['a'], status['b']):
                self.sim.advance(100)
            else:
                self.sim.advance(10)
        self.fail('%s not reached within the %g s hang bound: %s' % (what, bounds.WAIT_S, self.sim.status()))

    def restarted(self, boots):
        """Until B booted again after a restart and its endpoint listens (home, USB on)."""
        return self.wait(lambda s: s['b_boots'] > boots and s['listening'], what='B back home')

    def write(self, dev, data, path):
        self.assertEqual(dev.stage(data), 0)
        return dev.file_write(path, hashlib.sha256(data).digest(), timeout=10.0)

    # -- protocol -------------------------------------------------------------------------------
    @pytest.mark.sim
    def test_hello_is_the_generated_text_and_state_comes_first(self):
        """Kept: HELLO over the USB endpoint is the first contract every host relies on."""
        dev = self.connect(hello=False)
        for kind in (P.STATUS, P.READ_FILE, P.PUT_BEGIN, P.REBOOT, 0x5F):
            self.assertEqual(dev.call(kind, b'')[0], ids.S_STATE, kind)  # before HELLO: state, nothing done
        code, text = dev.hello()
        self.assertEqual(
            (code, text),
            (
                0,
                P.hello_text(
                    release.VERSION,
                    None,
                    'product',  # an empty boot partition
                    xyf_build.usb_switches('xyf-product'),
                    confirm=0,
                ),
            ),
        )  # sim.seed
        self.assertEqual(P.Hello(text).cmds, P.answered(xyf_build.usb_switches('xyf-product')))
        dev.hello_info = None  # xyf_host refuses types missing from cmds
        self.assertEqual(dev.call(0x5F, b''), (ids.S_UNSUPPORTED, b''))  # not in cmds: status only
        self.assertEqual(self.sim.status()['b_boots'], 1)

    @pytest.mark.sim
    def test_reads_screen_peek_status(self):
        """Kept: Host reads (screen, peek, status) cross B and come back whole."""
        dev = self.connect()
        code, body = dev.call(P.STATUS)
        decoded = host.decode_status(body)
        self.assertEqual((code, decoded['usb_service']['version'], decoded['usb_service']['identity']), (0, 6, '0x0'))
        self.assertEqual(dev.read_file(BASE + 'state-a.txt'), (0, self.file(BASE + 'state-a.txt')))
        os_ldr = BASE + 'firmware/opxy-121/os.ldr'
        data = b''.join(dev.read_file(os_ldr, offset)[1] for offset in range(0, 8192, 1024))
        self.assertEqual(data, self.file(os_ldr)[:8192])
        self.assertEqual(dev.read_file(BASE + 'missing.txt')[0], ids.S_NOT_FOUND)
        self.assertEqual(dev.read_file('/fat32/x')[0], ids.S_BAD_REQUEST)
        self.assertEqual(dev.read_file(os_ldr)[0], ids.S_TOO_LARGE)  # whole form above 4096 B
        code, body = dev.call(P.SCREEN, struct.pack('<I', 0))
        self.assertEqual((code, len(body)), (0, 4 + 1024))
        self.assertEqual(dev.call(P.SCREEN, struct.pack('<I', 0x3C000))[0], ids.S_RANGE)
        code, body = dev.call(P.PEEK, struct.pack('<II', 0x8BFFF000, 16))
        self.assertEqual(code, 0)
        self.assertEqual(dev.call(P.PEEK, struct.pack('<II', 0x11A00000, 16))[0], ids.S_RANGE)  # L1 instruction

    @pytest.mark.sim
    def test_staging_and_file_write(self):
        """Kept: A file write crosses host, B staging, the link and A storage byte for byte."""
        dev = self.connect(startup_ready=True)
        self.assertEqual(dev.call(P.PUT_DATA, struct.pack('<I', 0) + b'x')[0], ids.S_STATE)  # nothing announced
        self.assertEqual(dev.call(P.FILE_WRITE, (BASE + 'x.bin\0').encode() + bytes(32))[0], ids.S_STATE)
        data = bytes(range(256)) * 10
        path = BASE + 'log/sim/a.bin'
        self.assertEqual(self.write(dev, data, path), (0, len(data), zlib.crc32(data)))
        self.assertEqual(self.file(path), data)
        # file.write_staged checks the length first (bad_request), then the path (refused).
        payload = b'/yaffs2/upgrade/outside.bin\0' + hashlib.sha256(data).digest()
        self.assertEqual(dev.call(P.FILE_WRITE, payload[:-1])[0], ids.S_BAD_REQUEST)
        self.assertEqual(dev.call(P.FILE_WRITE, payload)[0], ids.S_REFUSED)
        self.assertEqual(dev.call(P.FILE_WRITE, (BASE + 'x.bin\0').encode() + bytes(32))[0], ids.S_SHA)
        self.assertEqual(dev.call(P.PUT_BEGIN, struct.pack('<II', 0, 0) + bytes(32))[0], ids.S_TOO_LARGE)
        self.assertEqual(dev.call(P.PUT_BEGIN, struct.pack('<II', 4, 0) + bytes(32))[0], 0)
        self.assertEqual(dev.call(P.PUT_DATA, struct.pack('<I', 1) + b'x')[0], ids.S_RANGE)
        self.assertEqual(dev.call(P.PUT_END)[0], ids.S_STATE)  # incomplete: discarded

    @pytest.mark.sim
    def test_restart_dry_run_and_continue_link(self):
        """Kept: Reset: restart, dry run and continue keep the link and the host session working."""
        dev = self.connect()
        self.assertEqual(dev.call(P.HOLD, struct.pack('<I', 1))[0], 0)
        self.assertEqual(dev.call(P.CONTINUE, struct.pack('<I', 2))[0], ids.S_LINK)  # modes >= 2: link
        self.assertEqual(dev.call(P.REBOOT, struct.pack('<I', 3))[0], ids.S_BAD_REQUEST)
        boots = self.sim.status()['b_boots']
        self.assertEqual(dev.call(P.REBOOT, struct.pack('<I', 2))[0], 0)
        self.restarted(boots)
        dev = self.connect()
        usb = host.decode_status(dev.call(P.STATUS)[1])['usb_service']
        self.assertEqual((usb['present'], usb['crash_restarts']), (3 | 1 << 4, 0))  # marker: home
        code, marker = dev.call(P.PEEK, struct.pack('<II', 0x8BFFF000, 16))
        magic, inverse, reason, _ = struct.unpack('<4I', marker[4:20])
        self.assertEqual((magic, reason & 0x100), (0x52465859, 0x100))  # held, carried
        boots = self.sim.status()['b_boots']
        self.assertEqual(dev.call(P.CONTINUE, struct.pack('<I', 1))[0], 0)
        self.restarted(boots)
        dry = host.decode_status(self.connect().call(P.STATUS)[1])['dry_run_file_boot']
        self.assertEqual(
            (dry['outcome'], dry['committed'], dry['image_bytes']), ('file', 1, sim.STOCK_OS.stat().st_size)
        )

    @pytest.mark.sim
    def test_continue_boots_the_default_and_plain_power_on_does_too(self):
        """Kept: Reset: continue and a plain power-on both boot the default entry."""
        dev = self.connect()
        self.assertEqual(dev.call(P.CONTINUE)[0], 0)
        self.wait(lambda s: s['a'] == 'os' and s['b'] == 'os' and not s['listening'], what='both in the OS')
        self.sim.control('power plain')  # no PLAY: straight to the default
        self.wait(lambda s: s['power_ons'] == 2 and s['a'] == 'os' and s['b'] == 'os', what='plain boot')
        self.sim.control('power play')
        self.wait(lambda s: s['listening'], what='menu with USB')

    # -- the file engine on A, B's checks bypassed ----------------------------------
    def a_request(self, cls, op, argument=0, path=None, body=b'', timeout=3.0):
        """One raw request to A from the board (B never sees it): (result, errno) or None."""
        return self.sim.ask_a(cls, op, argument, 0, raw_path(path) if path is not None else body, timeout)

    def command(self, item):
        """One command item to A from the board (as b's ask(): partition 0x18 with the command tag);
        True once A acknowledged it."""
        type(self).sequence = getattr(type(self), 'sequence', 0) + 1
        value = COMMAND_TAG << 16 | item << 8 | (self.sequence & 0xFF)
        reply = self.sim.ask_a(PARTITION_CLASS, 0x18, value)
        return reply is not None and reply[0] & 0xFFFFFFFF == ~value & 0xFFFFFFFF

    def write_entry(self):
        """A's write entry with its read-write window open, entered from the board (B bypassed).
        Op 8 is the read-only mount; the first change opens the window (here a mkdir of an
        existing folder: the window opens, then the folder exists)."""
        self.assertTrue(self.command(FILE_WRITE_ITEM))
        self.assertEqual(self.a_request(PARTITION_CLASS, 8), (0, 0))
        self.assertEqual(self.sim.status()['rw_open'], 0)
        self.assertEqual(self.a_request(PATH_CLASS, 0x26, path=BASE + 'log')[0], -1)
        self.assertEqual(self.sim.status()['rw_open'], 1)

    def hold_window(self, dev, hold_ms, probe):
        """diag.engine_test in a thread (A's run entry holds the read-write window for hold_ms);
        returns (thread, reply) once A deletes probe, i.e. the window is open. The clock is held (hold_clock): B's
        hold ends only by the test's advance, never at the host's pace while the test acts in the window."""
        reply = {}
        self.sim.set('hold_clock', 1)
        # The call waits on the test's own steps, not on the simulator alone: no SIM_CALL_S cap (timeout: a hang bound).
        dev.sim_call_s = None

        def run():
            try:
                reply['r'] = dev.call(P.MTP_TEST, struct.pack('<I', hold_ms), timeout=20.0 + hold_ms / 1000.0)
            except Exception as e:  # a power cycle ends it
                reply['error'] = e

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        self.sim.until(
            lambda: self.a_request(PATH_CLASS, 0x25, path=probe) == (0, 0), 'the read-write window open', 5000
        )
        return thread, reply

    @pytest.mark.sim
    def test_engine_refuses_brick_paths_on_a_with_b_bypassed(self):
        """Kept: Protected paths: A refuses brick paths itself, even with B bypassed."""
        root = self.dir / 'yaffs2'
        (root / 'bootloader.dev').write_bytes(b'dev menu\n')
        for name in ('probe-1', 'probe-2', 'xy-forever/log/old.bin'):
            (root / name).write_bytes(b'x')
        (root / 'emptydir').mkdir()
        bootpart = self.dir / 'bootpart.bin'
        before = bootpart.read_bytes() if bootpart.exists() else None
        dev = self.connect(startup_ready=True)
        # The read-only entry (file.read latched it): a change is refused for the closed window
        # (EROFS); a brick path and the raw layer are refused by the policy first (EACCES).
        self.assertEqual(dev.read_file(BASE + 'state-a.txt')[0], 0)
        self.assertEqual(self.a_request(PATH_CLASS, 0x25, path='/yaffs2/bootloader.dev'), (-1, EACCES))
        self.assertEqual(self.a_request(PATH_CLASS, 0x27, path='/yaffs2'), (-1, EACCES))
        self.assertEqual(self.a_request(PATH_CLASS, 0x25, path='/yaffs2/probe-1'), (-1, EROFS))
        self.assertEqual(self.a_request(PATH_CLASS, 0x29, 0x8301, path='/yaffs2/probe-1'), (-1, EROFS))
        self.assertEqual(self.a_request(PATH_CLASS, 0x29, 0, path='/fat32/x'), (-1, EACCES))
        for cls, op, argument, body in REFUSED_RAW:
            self.assertEqual(self.a_request(cls, op, argument, body=body), (-1, EACCES), (hex(cls), hex(op)))
        # The run entry (diag.engine_test's held read-write window): every brick path refused...
        thread, reply = self.hold_window(dev, 4000, '/yaffs2/probe-1')
        for path in MARKER_SPELLINGS + ROOT_SPELLINGS:
            for op in (0x25, 0x27):  # unlink, rmdir
                self.assertEqual(self.a_request(PATH_CLASS, op, path=path), (-1, EACCES), (path, hex(op)))
        for path in (
            '/fat32/bootloader.dev',
            '/yaffs2/a/../bootloader.dev',
            '/yaffs2/./bootloader.dev',
            '/YAFFS2/bootloader.dev',
            '/yaffs2/bootloader.dev\x01',
        ):  # (length: the stock body copy cuts)
            self.assertEqual(self.a_request(PATH_CLASS, 0x25, path=path), (-1, EACCES), path)
        for cls, op, argument, path in (
            (PARTITION_CLASS, 0x36, 0, None),
            (PARTITION_CLASS, 0x34, 0, None),
            (PATH_CLASS, 0x35, 0, '/yaffs2/probe-2'),
            (PATH_CLASS, 0x28, 0, '/yaffs2'),
        ):
            self.assertEqual(self.a_request(cls, op, argument, path=path), (-1, EACCES), hex(op))  # not exposed
        self.assertEqual(self.a_request(PARTITION_CLASS, 0x2D, 77), (-1, EBADF))  # a handle a never opened
        self.assertEqual(self.a_request(SECTOR_CLASS, 0x2F, 0x1234), (-1, EBADF))  # readdir: refused
        self.assertEqual((root / 'bootloader.dev').read_bytes(), b'dev menu\n')
        # The write rule: the marker is never written either.
        self.assertEqual(self.a_request(PATH_CLASS, 0x29, 0x8301, path='/yaffs2//bootloader.dev'), (-1, EACCES))
        self.assertEqual((root / 'bootloader.dev').read_bytes(), b'dev menu\n')
        # ...and everything else allowed: delete XY Forever's own files, folders.
        self.assertEqual(self.a_request(PATH_CLASS, 0x25, path='/yaffs2/xy-forever/log/old.bin'), (0, 0))
        self.assertEqual(self.a_request(PATH_CLASS, 0x26, path='/yaffs2/newdir'), (0, 0))
        self.assertEqual(self.a_request(PATH_CLASS, 0x27, path='/yaffs2/emptydir'), (0, 0))
        self.assertFalse((root / 'xy-forever/log/old.bin').exists() or (root / 'emptydir').exists())
        self.assertTrue((root / 'newdir').is_dir())
        self.sim.advance(4000)  # the hold's end, not waited for (B waits on A's reply)
        self.sim.set('hold_clock', 0)
        thread.join(30.0)
        code, body = reply['r']
        self.assertEqual((code, struct.unpack('<16I', body[:64])[0]), (0, 11))  # the self-test still passes
        self.assertTrue((root / 'bootloader.dev').exists())
        # The raw layer in the run entry is b's bootloader asking (b restarted alone): a never
        # answers or runs it; it closes storage and restarts.
        boots = self.sim.status()['a_boots']
        thread, reply = self.hold_window(dev, 3000, '/yaffs2/probe-2')
        tag = self.sim.inject(PARTITION_CLASS, 0x14, 0)
        self.wait(lambda s: s['a_boots'] > boots, what='A restarted')
        self.sim.set('hold_clock', 0)
        self.assertFalse(self.sim.injected(tag)['done'])
        self.assertEqual(bootpart.read_bytes() if bootpart.exists() else None, before)
        self.assertTrue((root / 'bootloader.dev').exists())
        self.sim.control('power')
        self.wait(lambda s: s['listening'], what='menu with USB after the power cycle')

    @pytest.mark.sim
    def test_boot_requests_are_held_and_a_restarts(self):
        """Kept: Reset: boot requests are held until A restarts. F1: outside install mode each request of the stock bootloader's boot read (b
        restarted alone) is held unanswered in every A mode: A closes storage and restarts, and its
        bootloader would answer it. B (here not actually restarted) never restarts itself: it
        stays at the menu on USB, file commands answer link; a power cycle brings both back. Nothing
        reaches a boot partition."""
        bootpart = self.dir / 'bootpart.bin'
        before = bootpart.read_bytes() if bootpart.exists() else None
        modes = ('read entry', 'write entry', 'image mode')
        for (cls, op, argument, body), mode in zip(BOOT_REQUESTS, modes):
            with self.subTest(op=hex(op), mode=mode):
                dev = self.connect()
                status = self.sim.status()
                if mode == 'read entry':
                    self.assertEqual(dev.read_file(BASE + 'state-a.txt')[0], 0)
                elif mode == 'write entry':
                    self.write_entry()
                tag = self.sim.inject(cls, op, argument, 0, body)
                self.wait(lambda s: s['a_boots'] > status['a_boots'], what='A restarted')
                self.assertFalse(self.sim.injected(tag)['done'])
                self.sim.advance(3000)  # B's keepalives and patience, on the device clock
                self.wait(lambda s: s['a'] == 'running' and s['a_bootloader'] == 1, what="A's bootloader")
                after = self.sim.status()
                self.assertEqual((after['rw_open'], after['unclean_mounts'], after['boot_writes']), (0, 0, 0))
                self.assertEqual((after['b_boots'], after['b'], after['listening']), (status['b_boots'], 'running', 1))
                self.assertEqual(
                    dev.call(P.READ_FILE, (BASE + 'state-a.txt').encode() + b'\0', timeout=10.0)[0], ids.S_LINK
                )
                self.sim.control('power')  # the way out: a power cycle
                self.wait(
                    lambda s: s['listening'] and s['a'] == 'running' and s['b'] == 'running',
                    what='menu with USB after the power cycle',
                )
                dev = self.connect()
                self.assertEqual(dev.read_file(BASE + 'state-a.txt')[0], 0)
        self.assertFalse(any(bootpart.read_bytes()) if bootpart.exists() else False)
        self.assertIsNone(before)

    @pytest.mark.sim
    def test_no_engine_request_while_a_boot_partition_is_selected(self):
        """Kept: Protected paths: no file request runs while a boot partition is selected. The review's sequence: install mode selects boot partition 1, then the file-write
        item. Before, op 8 opened the read-write window there and a write open and a mkdir ran
        while the chip addressed the boot partition. Now every engine request is refused (and the
        select back to 0 too: the write entry never serves the raw layer), until A restarts."""
        root = self.dir / 'yaffs2'
        self.connect(startup_ready=True)
        self.assertTrue(self.command(INSTALL_ITEM))
        self.assertEqual(self.a_request(PARTITION_CLASS, 0x18, 1), (0, 0))
        self.assertEqual(self.sim.status()['partition'], 1)
        self.assertEqual(self.a_request(PATH_CLASS, 0x29, 0, path=BASE + 'state-a.txt'), (-1, EACCES))
        self.assertTrue(self.command(FILE_WRITE_ITEM))
        for request in (
            (PARTITION_CLASS, 8, 0, None),
            (PATH_CLASS, 0x29, 0x8301, '/yaffs2/new.bin'),
            (PATH_CLASS, 0x26, 0, '/yaffs2/newdir'),
            (PATH_CLASS, 0x29, 0, BASE + 'state-a.txt'),
            (PARTITION_CLASS, 9, 0, None),
        ):
            cls, op, argument, path = request
            self.assertEqual(self.a_request(cls, op, argument, path=path), (-1, EACCES), hex(op))
        status = self.sim.status()
        self.assertEqual((status['partition'], status['boot_writes']), (1, 0))
        self.assertFalse((root / 'new.bin').exists() or (root / 'newdir').exists())
        # The select back to 0 outside install mode is a boot request: b's bootloader to A (F1),
        # never answered: A restarts (its bootloader selects for itself).
        boots = self.sim.status()['a_boots']
        self.assertIsNone(self.a_request(PARTITION_CLASS, 0x18, 0, timeout=1.0))
        self.wait(lambda s: s['a_boots'] > boots, what='A restarted')
        self.assertEqual(self.sim.status()['boot_writes'], 0)
        self.sim.control('power')
        self.wait(lambda s: s['listening'] and s['partition'] == 0, what='menu with USB after the power cycle')

    # -- hardening -------------------------------------------------------
    @pytest.mark.sim
    def test_failed_select_counts_as_selected(self):
        """Kept: Protected paths: a failed partition select still counts as selected. A boot-partition select that fails may still have switched the chip (the hook: the stock
        select switches, then answers 1). A refuses storage from the attempt on, and refuses boot
        writes until a select succeeds; a select of 0 that succeeds ends it."""
        self.connect(startup_ready=True)
        self.assertTrue(self.command(INSTALL_ITEM))
        self.sim.set('fail_select', 1)
        self.assertEqual(self.a_request(PARTITION_CLASS, 0x18, 1), (1, 0))  # failed, but switched
        self.assertEqual(self.sim.status()['partition'], 1)
        for cls, op, argument, path, body in (
            (PARTITION_CLASS, 8, 0, None, b''),
            (PATH_CLASS, 0x29, 0, BASE + 'state-a.txt', b''),
            (PARTITION_CLASS, 0x14, 0, None, b''),
            (WRITE_CLASS, 0x16, 1, None, bytes(512)),
        ):
            self.assertEqual(self.a_request(cls, op, argument, path=path, body=body), (-1, EACCES), hex(op))
        self.assertEqual(self.sim.status()['boot_writes'], 0)
        self.assertEqual(self.a_request(PARTITION_CLASS, 0x18, 0), (0, 0))  # back to the user area
        self.assertEqual(self.a_request(PARTITION_CLASS, 8), (0, 0))
        fd, error = self.a_request(PATH_CLASS, 0x29, 0, path=BASE + 'state-a.txt')
        self.assertGreaterEqual(fd, 0, error)
        self.assertEqual(self.a_request(PARTITION_CLASS, 0x2D, fd), (0, 0))

    @pytest.mark.sim
    def test_write_entry_refuses_brick_paths_with_b_bypassed(self):
        """Kept: Protected paths: A refuses library writes to brick paths with B bypassed. The file-write item's entry (one read-write window), B's checks bypassed: every
        brick path refused, the rest of the write area allowed (A keeps to B's write area too:
        test_write_area_on_a_with_b_bypassed)."""
        root = self.dir / 'yaffs2'
        (root / 'bootloader.dev').write_bytes(b'dev menu\n')
        (root / 'xy-forever/probe').write_bytes(b'x')
        (root / 'xy-forever/emptydir').mkdir()
        bootpart = self.dir / 'bootpart.bin'
        before = bootpart.read_bytes() if bootpart.exists() else None
        self.connect(startup_ready=True)
        self.write_entry()
        for path in MARKER_SPELLINGS + ROOT_SPELLINGS:
            for op in (0x25, 0x27):
                self.assertEqual(self.a_request(PATH_CLASS, op, path=path), (-1, EACCES), (path, hex(op)))
        for path in ('/fat32/x', '/yaffs2/../x', '/yaffs2/./bootloader.dev', '/YAFFS2/bootloader.dev', '/yaffs2/a\x1b'):
            self.assertEqual(self.a_request(PATH_CLASS, 0x29, 0x8301, path=path), (-1, EACCES), path)
        for cls, op, argument, body in REFUSED_RAW:  # refused (boot requests: b's restart, F1)
            self.assertEqual(self.a_request(cls, op, argument, body=body), (-1, EACCES), (hex(cls), hex(op)))
        for cls, op, path in (
            (PARTITION_CLASS, 0x36, None),
            (PARTITION_CLASS, 0x34, None),
            (PATH_CLASS, 0x35, '/yaffs2/probe'),
            (PATH_CLASS, 0x28, '/yaffs2'),
        ):
            self.assertEqual(self.a_request(cls, op, path=path), (-1, EACCES), hex(op))
        self.assertEqual(self.sim.status()['a'], 'running')
        # Allowed: a new file in content/, written and closed; a folder made and one removed; a file
        # deleted. The marker is not overwritten (a name holding "bootloader").
        fd, error = self.a_request(PATH_CLASS, 0x29, 0x8301, path='/yaffs2/content/projects-new.bin')
        self.assertGreaterEqual(fd, 0, error)
        self.assertEqual(self.a_request(WRITE_CLASS, 0x2B, fd, body=b'outside xy-forever'), (18, 0))
        self.assertEqual(self.a_request(PARTITION_CLASS, 0x2D, fd), (0, 0))
        self.assertEqual((root / 'content/projects-new.bin').read_bytes(), b'outside xy-forever')
        self.assertEqual(self.a_request(PATH_CLASS, 0x26, path=BASE + 'newdir'), (0, 0))
        self.assertEqual(self.a_request(PATH_CLASS, 0x27, path=BASE + 'emptydir'), (0, 0))
        self.assertEqual(self.a_request(PATH_CLASS, 0x25, path=BASE + 'probe'), (0, 0))
        self.assertEqual(self.a_request(PATH_CLASS, 0x29, 0x8301, path='/yaffs2/bootloader.dev'), (-1, EACCES))
        self.assertEqual(self.a_request(PARTITION_CLASS, 9), (3, 0))  # 3 = closed the window
        self.assertTrue((root / 'xy-forever/newdir').is_dir())
        self.assertFalse((root / 'xy-forever/emptydir').exists() or (root / 'xy-forever/probe').exists())
        self.assertEqual((root / 'bootloader.dev').read_bytes(), b'dev menu\n')
        self.assertEqual(bootpart.read_bytes() if bootpart.exists() else None, before)
        self.assertEqual(self.sim.status()['rw_open'], 0)

    # every way out of a boot closes the read-write window first (a reset or a boot with it
    # open is a power cut for yaffs: no checkpoint, a full scan at the next mount).
    @pytest.mark.sim
    def test_reboot_closes_the_write_window(self):
        """Kept: Reset: a reboot closes the write window first."""
        dev = self.connect(startup_ready=True)
        self.write_entry()
        boots = self.sim.status()['b_boots']
        self.assertEqual(dev.call(P.HOLD, struct.pack('<I', 1))[0], 0)
        self.assertEqual(dev.call(P.REBOOT, struct.pack('<I', 2))[0], 0)
        status = self.restarted(boots)
        self.assertEqual((status['a_boots'], status['rw_open'], status['unclean_mounts']), (2, 0, 0))

    @pytest.mark.sim
    def test_silent_b_ends_the_session_with_the_write_window_closed(self):
        """Kept: Fault: a silent B ends the session with the write window closed. B goes silent (nothing reaches A; B's keepalives count, so an idle host never ends the
        session): after the session's idle window (10 s) A's session ends with the read-write window
        closed, and A does not restart. The device clock is advanced (control "advance"), not waited
        for: 8 s silent, A is still in its session (window open); 12 s, its session has ended."""
        self.connect(startup_ready=True)
        self.assertTrue(self.command(USB_MODE_ITEM))
        self.write_entry()
        self.sim.advance(200)  # A's loop has seen the last request
        self.sim.set('a_silent', 1)
        self.sim.advance(8000)
        status = self.sim.status()
        self.assertEqual((status['a_boots'], status['a'], status['rw_open']), (1, 'running', 1))
        self.sim.advance(4000)
        status = self.wait(lambda s: s['a'] != 'running', what='A out of its session')
        self.assertEqual(status['a_boots'], 1)
        self.sim.set('a_silent', 0)
        self.assertEqual((status['rw_open'], status['unclean_mounts']), (0, 0))
        self.sim.control('power')
        self.wait(lambda s: s['listening'], what='menu with USB after the power cycle')

    # -- the write window (A opens it on the first change and keeps it; B closes it once no
    # write came for SETTLE_MS, A on its own after IDLE_MS; every other command item closes it) ---
    SETTLE_MS, IDLE_MS = 8000, 10000  # storage_server.hpp window_settle_ms / window_idle_ms

    def menu_catalog(self, dev):
        """The menu's catalog as B holds it (PEEK of its buffer), decoded."""
        data = b''
        for at in (0, 1024):
            code, body = dev.call(P.PEEK, struct.pack('<II', census.CATALOG_ADDRESS + at, 1024))
            self.assertEqual(code, 0)
            data += body[4:]
        return census.catalog(data)

    def b_window(self, dev):
        rr = host.decode_status(dev.call(P.STATUS)[1])['remote_read']
        return rr['b_window'], rr['b_windows'], rr['b_catalogs']

    @pytest.mark.sim
    def test_failed_window_close_is_reported_and_tried_again(self):
        """Kept: Fault: a failed write-window close is reported and retried. Every yaffs unmount fails: B's close gets 0 (status b_window 0), the window stays open;
        once unmounts work again A's own idle close takes it."""
        dev = self.connect(startup_ready=True)
        self.assertEqual(self.write(dev, b'close\n', BASE + 'log/w/close.txt')[0], 0)
        self.sim.set('fail_unmount', 1)
        self.sim.advance(self.SETTLE_MS + 1000)
        self.wait(lambda s: s['rw_open'] == 1, what='settle tried')
        deadline = time.monotonic() + 10
        while self.b_window(dev)[0] != 0 and time.monotonic() < deadline:
            self.sim.advance(50)
        self.assertEqual(self.b_window(dev), (0, 0, 1))
        self.assertEqual(self.sim.status()['rw_open'], 1)
        self.sim.set('fail_unmount', 0)
        self.sim.advance(self.IDLE_MS + 1000)
        status = self.wait(lambda s: s['rw_open'] == 0, what="A's idle close")
        self.assertEqual(status['unclean_mounts'], 0)

    @pytest.mark.sim
    def test_power_cut_with_the_window_open(self):
        """Kept: Power cut with the write window open: the next start repairs. A cut while the window is open, after a write completed: no checkpoint (the next mount is
        unclean: on the device a full scan), storage mounts, the file is the new one. A cut inside an
        in-place write leaves what reached storage (test_power_cut_between_storage_operations); old
        or new for every cut needs tmp/ and rename (#4)."""
        dev = self.connect(startup_ready=True)
        self.assertEqual(self.write(dev, b'old\n' * 100, BASE + 'log/w/cut.txt')[0], 0)
        self.sim.advance(self.SETTLE_MS + 1000)
        self.wait(lambda s: s['rw_open'] == 0, what='the window closed')
        new = b'new\n' * 300
        self.assertEqual(self.write(dev, new, BASE + 'log/w/cut.txt')[0], 0)
        self.assertEqual(self.sim.status()['rw_open'], 1)
        self.sim.control('power')
        status = self.wait(lambda s: s['listening'], what='menu with USB after the power cut')
        self.assertEqual(status['unclean_mounts'], 1)
        dev = self.connect()
        self.assertEqual(dev.read_file(BASE + 'log/w/cut.txt'), (0, new))
        self.assertEqual(dev.read_file(BASE + 'state-a.txt')[0], 0)

    # -- fault hooks ----------------------------------------------------------------------------
    # B's own file requests end within their bound (file_client.cpp): a silent, slow or
    # lossy A is an error to the caller (link), and B's menu keeps answering USB.
    # 191 (incident 2026-11-16): A's storage start. After an unclean shutdown A's read-only mount scans
    # the whole volume (4.5-17 s seen) or fails; B's menu must open at once, answer USB, and recover.
    def storage_start(self, dev):
        return host.decode_status(dev.call(P.STATUS)[1])['storage_start']

    def reboot_home(self, dev):
        boots = self.sim.status()['b_boots']
        self.assertEqual(dev.call(P.REBOOT, struct.pack('<I', 2))[0], 0)
        self.restarted(boots)
        return self.connect()

    def storage_phase(self, dev, phase, advance=0):
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            st = self.storage_start(dev)
            if st['phase'] == phase:
                return st
            if advance:
                self.sim.advance(advance)
            else:
                self.sim.advance(50)
        self.fail('storage phase %r not reached: %s' % (phase, st))

    @pytest.mark.sim
    def test_failed_mount_recovers_without_a_power_cycle(self):
        """Kept: Fault: a failed mount recovers without a power cycle. The boot's read-only mount fails: the menu says so, file commands answer storage (as on the
        device), and B asks again every retry_ms: A mounts again and the library appears."""
        dev = self.connect()
        self.sim.set('fail_mount', 1)  # the next yaffs mount on A fails
        dev = self.reboot_home(dev)
        st = self.storage_phase(dev, 'A mount failed, asking again')
        self.assertEqual((st['a_mount']['result'], st['a_mount']['attempts']), ('mount', 1))
        self.assertEqual(st['a_mount']['repair'], {'state': 'not needed'})  # 192: only after write_attempt
        self.assertEqual(self.menu_catalog(dev)['items'], [])
        self.assertEqual(dev.read_file(BASE + 'state-a.txt')[0], ids.S_STORAGE)
        self.assertEqual(dev.hello()[0], 0)
        st = self.storage_phase(dev, 'up', advance=1000)
        self.assertEqual((st['a_mount']['result'], st['a_mount']['attempts']), ('ok', 2))
        self.assertTrue(self.menu_catalog(dev)['items'])
        self.assertEqual(dev.read_file(BASE + 'state-a.txt')[0], 0)

    # 192: after an unclean shutdown yaffs has no checkpoint and its read-only mount wants to repair
    # (denied: write_attempt). A repairs once per boot (read-write mount, unmount, read-only mount); never
    # for another failure; a clean mount writes nothing.

    @pytest.mark.sim
    def test_unclean_shutdown_is_repaired_without_a_power_cycle(self):
        """Kept: Fault: an unclean mount is repaired without a power cycle. The read-only mount fails with write_attempt; B shows the repair and asks again at once; A
        repairs (here a slow checkpoint) while the menu answers USB; then the library appears."""
        dev = self.connect()
        rw = self.sim.status()['rw_mounts']
        self.sim.set('needs_repair', 1)
        self.sim.set('unmount_ms', 600000)  # the repair's checkpoint: until cleared
        dev = self.reboot_home(dev)
        self.wait(lambda s: s['rw_open'] == 1, what='the repair mounted read-write')
        st = self.storage_start(dev)
        self.assertEqual(st['phase'], 'A mount failed, asking again')
        self.assertEqual(
            (st['a_mount']['result'], st['a_mount']['attempts'], st['a_mount']['repair']),
            ('write_attempt', 1, {'state': 'pending'}),
        )
        self.assertGreater(st['a_mount']['denied_writes'], 0)
        self.assertEqual(st['asks'], 2)  # asked again at once (no retry_ms wait)
        self.assertEqual(dev.hello()[0], 0)  # the menu answers during the repair
        self.sim.set('unmount_ms', 0)  # the checkpoint ends
        st = self.storage_phase(dev, 'up')
        self.assertEqual(
            (
                st['a_mount']['result'],
                st['a_mount']['attempts'],
                st['a_mount']['repair']['state'],
                st['a_mount']['repair']['result'],
            ),
            ('ok', 2, 'done', 'ok'),
        )
        status = self.sim.status()
        self.assertEqual((status['needs_repair'], status['rw_open'], status['rw_mounts']), (0, 0, rw + 1))
        self.assertTrue(self.menu_catalog(dev)['items'])
        self.assertEqual(dev.read_file(BASE + 'state-a.txt')[0], 0)
        dev = self.reboot_home(dev)  # repaired: the next boot is clean
        st = self.storage_phase(dev, 'up')
        self.assertEqual((st['a_mount']['attempts'], st['a_mount']['repair']), (1, {'state': 'not needed'}))

    @pytest.mark.sim
    def test_power_cut_during_the_repair_repairs_again(self):
        """Kept: Power cut during the repair: the next start repairs again."""
        dev = self.connect()
        self.sim.set('needs_repair', 1)
        self.sim.set('unmount_ms', 600000)
        dev = self.reboot_home(dev)
        self.wait(lambda s: s['rw_open'] == 1, what='the repair mounted read-write')
        boots = self.sim.status()['b_boots']
        self.sim.control('cut')  # before the checkpoint
        self.sim.set('unmount_ms', 0)
        self.assertEqual(self.sim.status()['needs_repair'], 1)
        self.restarted(boots)
        dev = self.connect()
        st = self.storage_phase(dev, 'up')
        self.assertEqual(
            (st['a_mount']['result'], st['a_mount']['attempts'], st['a_mount']['repair']['state']), ('ok', 2, 'done')
        )
        self.assertEqual(self.sim.status()['needs_repair'], 0)
        self.assertTrue(self.menu_catalog(dev)['items'])

    def settled(self, dev):
        """B closes a read's file after its reply and takes the next command only then: once this
        one is answered the link is idle, and a fault set now hits the next command, not that close."""
        self.assertEqual(dev.call(P.STATUS)[0], 0)

    @staticmethod
    def fault_taken(before, now):
        """A one-shot fault fired, a request was held in B's driver or kept from a silent A."""
        return (
            any(before[f] > 0 and now[f] == 0 for f in ('drop_request', 'drop_body', 'cut_body'))
            or now['b_queued'] > before['b_queued']
            or now['a_silenced'] > before['a_silenced']
        )

    def call_while_advancing(self, dev, kind, payload, ms, timeout=8.0):
        """dev.call in a thread while the device clocks jump ms ahead (B waits on A's reply)."""
        reply = {}

        def run():
            try:
                reply['r'] = dev.call(kind, payload, timeout=timeout)
            except Exception as e:
                reply['error'] = e

        before = self.sim.status()
        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        # The jump counts only once B waits on the request the fault keeps unanswered: the fault took
        # effect, then B read its clock on (its bound starts from a reading after the request left),
        # or the call already ended. Host time only bounds a hang.
        deadline, reads = time.monotonic() + 60.0, None
        while thread.is_alive():
            now = self.sim.status()
            if reads is None and self.fault_taken(before, now):
                reads = now['b_clock_reads']
            if reads is not None and now['b_clock_reads'] - reads >= 100:
                break
            self.assertLess(time.monotonic(), deadline, 'B never waited on the request: %s' % now)
            self.sim.advance(5)
        self.sim.advance(ms)
        thread.join(timeout + 1.0)
        self.assertIn('r', reply, reply.get('error'))
        return reply['r']

    def lost_but_answering(self, dev):
        """B stays in its menu: file commands get link at once, everything else is answered. A, silent
        past its idle window, has restarted to wait for B. Past the lost limit (60 s on the
        device clock after the request left) B still does not restart (the former F2 restarted it): the
        menu stays on USB, crash restarts 0; a power cycle brings files back."""
        boots = self.sim.status()['b_boots']
        self.sim.advance(40300)
        path = (BASE + 'state-a.txt').encode() + b'\0'
        self.assertEqual(dev.call(P.READ_FILE, path, timeout=2.0)[0], ids.S_LINK)
        code, body = dev.call(P.STATUS, timeout=2.0)
        self.assertEqual(code, 0)
        status = self.sim.status()
        self.assertEqual((status['b'], status['listening'], status['b_boots']), ('running', 1, boots))
        self.sim.advance(21300)
        status = self.sim.status()
        self.assertEqual(
            (status['b'], status['listening'], status['b_boots'], status['b_waits_a']), ('running', 1, boots, 0)
        )
        usb = host.decode_status(dev.call(P.STATUS, timeout=2.0)[1])['usb_service']
        self.assertEqual(usb['crash_restarts'], 0)
        self.assertEqual(dev.call(P.READ_FILE, path, timeout=2.0)[0], ids.S_LINK)
        self.sim.control('power')
        self.wait(
            lambda s: s['listening'] and s['a'] == 'running' and s['b'] == 'running',
            what='menu with USB after the power cycle',
        )
        self.assertEqual(self.connect().read_file(BASE + 'state-a.txt')[0], 0)

    @pytest.mark.sim
    def test_a_silent_is_a_link_error_and_usb_stays(self):
        """Kept: Link fault: a silent A is a link error and B keeps USB. Before B's timed file client a silent A halted B after ~1 s (keepalives unanswered). Now the file command
        ends with link within its bound and B's menu keeps answering USB."""
        dev = self.connect()
        path = (BASE + 'state-a.txt').encode() + b'\0'
        self.assertEqual(dev.read_file(BASE + 'state-a.txt')[0], 0)  # read mode on, mounted
        self.settled(dev)
        self.sim.set('a_silent', 1)  # A answers nothing (stock ISR echoes too)
        self.assertEqual(self.call_while_advancing(dev, P.READ_FILE, path, 6000)[0], ids.S_LINK)
        self.sim.set('a_silent', 0)
        self.lost_but_answering(dev)

    # requests held in B's SPI driver (b_queue) and answered after their bound, on the device
    # clock (advance), never on host time.
    def held_then_late(self, dev, payload, nth=1):
        """READ_FILE whose nth file request waits in B's busy driver past its 5 s bound (link), then
        goes out and A answers it late: until that answer has reached B."""
        self.settled(dev)
        self.sim.set('b_queue', nth)
        self.assertEqual(self.call_while_advancing(dev, P.READ_FILE, payload, 6000)[0], ids.S_LINK)
        self.assertEqual(self.sim.status()['b_queued'], 1)
        self.sim.set('b_queue', 0)
        self.wait(lambda s: s['b_queued'] == 0, what='the late answer back in B')

    def ranged(self, path, offset=0, length=64):
        return path.encode() + b'\0' + struct.pack('<II', offset, length)

    @pytest.mark.sim
    def test_a_slower_than_the_bound_then_a_late_answer(self):
        """Kept: Link fault: a late answer from A is never taken for the next request. A read's data answered after B's 5 s bound: link to the caller; the late body lands in the
        client's own buffer, frees the slot, and is never taken as the next answer: the next read
        (another file) gets its own bytes."""
        dev = self.connect()
        first, other = BASE + 'firmware/opxy-121/os.ldr', BASE + 'firmware/opxy-121-stock/info.txt'
        self.assertEqual(dev.read_file(BASE + 'state-a.txt')[0], 0)
        self.held_then_late(dev, self.ranged(first, 512), nth=3)  # open, seek pass; the read is late
        self.assertEqual(dev.read_file(other, 0, 64), (0, self.file(other)[:64]))
        self.assertNotEqual(self.file(first)[512:576], self.file(other)[:64])
        self.assertEqual(dev.read_file(first, 0, 64), (0, self.file(first)[:64]))
        self.assertEqual(self.sim.status()['b'], 'running')

    @pytest.mark.sim
    def test_read_after_a_restarted_alone_never_reaches_its_bootloader(self):
        """Kept: Reset: A restarted alone never gets a request while in its bootloader. A restarts alone and its stock bootloader serves the link (no path policy). B
        asks before every file command whether A is still XY Forever: link, and no file request
        reaches the bootloader (before: read mode stayed latched and the open went to it)."""
        dev = self.connect()
        self.assertEqual(dev.read_file(BASE + 'state-a.txt')[0], 0)  # read mode latched, mounted
        self.sim.set('a_boot_ms', 0)
        self.wait(lambda s: s['clock_ms'] - s['b_sent_ms'] < 60, what='a keepalive just answered')
        self.sim.control('reset a')
        self.wait(lambda s: s['a_bootloader'] == 1, what="A's stock bootloader on the link")
        self.assertEqual(dev.call(P.READ_FILE, raw_path(BASE + 'state-a.txt'), timeout=5.0)[0], ids.S_LINK)
        log = (self.dir / 'log.txt').read_text(errors='replace')
        self.assertNotIn("A's bootloader: class %08x" % PATH_CLASS, log)  # (B then takes the lost-link path)

    @pytest.mark.sim
    def test_dropped_request_and_link_error_mid_body_end_as_link(self):
        """Kept: Link fault: a dropped request or a mid-body error ends as a link error, never a hang. Before B's timed file client B waited for good. Now: link within the bound; the request stays out (later
        file commands: link at once) until the lost limit; USB answers throughout."""
        for fault in ('drop_request', 'drop_body'):  # drop_body: the reply, body and all, is lost
            dev = self.connect()
            path = (BASE + 'state-a.txt').encode() + b'\0'
            self.assertEqual(dev.read_file(BASE + 'state-a.txt')[0], 0)
            self.settled(dev)
            self.sim.set(fault, 1)
            payload = path if fault == 'drop_request' else path[:-1] + b'\0' + struct.pack('<II', 0, 64)
            self.assertEqual(self.call_while_advancing(dev, P.READ_FILE, payload, 6000)[0], ids.S_LINK, fault)
            self.assertEqual(self.sim.status()[fault], 0)
            self.assertEqual(dev.call(P.READ_FILE, path, timeout=2.0)[0], ids.S_LINK)  # the slot is still out
            self.assertEqual(dev.call(P.STATUS, timeout=2.0)[0], 0)
            self.lost_but_answering(dev)

    @pytest.mark.sim
    def test_link_cut_mid_body(self):
        """Kept: Link fault: a cut mid-body leaves both processors usable. Part of a read's reply body reaches B, then the link drops: link within the bound,
        nothing of the partial body is sent to the host, USB answers until the lost limit."""
        dev = self.connect()
        path = BASE + 'firmware/opxy-121/os.ldr'
        self.assertEqual(dev.read_file(BASE + 'state-a.txt')[0], 0)
        self.settled(dev)
        self.sim.set('cut_body', 100)  # 100 of the read's 1024 bytes
        code, body = self.call_while_advancing(dev, P.READ_FILE, self.ranged(path, 0, 1024), 6000)
        self.assertEqual((code, body), (ids.S_LINK, b''))
        self.assertEqual(self.sim.status()['cut_body'], 0)
        self.assertIn('cut after 100 of its 1024 body bytes', (self.dir / 'log.txt').read_text(errors='replace'))
        self.assertEqual(dev.call(P.READ_FILE, raw_path(path), timeout=2.0)[0], ids.S_LINK)
        self.lost_but_answering(dev)

    @pytest.mark.sim
    def test_cable_pulled_mid_frame(self):
        """Kept: USB fault: a cable pull mid-frame leaves B ready for the next host."""
        dev = self.connect()
        self.sim.set('cable_pull', 10)  # the device side: 10 bytes of the reply, then gone
        with self.assertRaises(OSError):
            dev.call(P.STATUS, timeout=2.0)
        dev = self.connect()
        self.assertEqual(dev.call(P.STATUS)[0], 0)
        # The host side: half a frame, then gone. The device drops the partial frame after
        # idle_reset_ms of silence, so the next request is answered at once.
        with socket.create_connection(('127.0.0.1', self.sim.port)) as s:
            s.sendall(P.encode(P.STATUS, 9, bytes(64))[:8])
        self.sim.advance(400)
        dev = self.connect(hello=False)
        self.assertEqual(dev.call(P.HELLO, timeout=0.5)[0], 0)

    @pytest.mark.sim
    def test_replies_that_cannot_leave_are_shown_and_counted(self):
        """Kept: #314: a USB link that stops answering while the menu runs is never silent. Replies that cannot be
        written (an IN urb that never completes) put a line on the menu until one gets out; the next STATUS counts it."""
        dev = self.connect()
        line = 'menu: usb line: USB: replies are not getting out. Reconnect the cable.'

        def log():
            return (self.dir / 'log.txt').read_text(errors='replace')

        self.sim.set('stall_replies', 1)
        dev.t.write(P.encode(P.STATUS, 7, b''))  # its reply cannot leave
        self.sim.until(lambda: line in log(), 'the stuck line', 5000)
        self.sim.set('stall_replies', 0)
        code, body = dev.call(P.STATUS)  # the first reply out: composed while the outage still ran
        self.assertEqual((code, host.decode_status(body)['usb_service']['replies_lost']['outages']), (0, 1))
        lost = host.decode_status(dev.call(P.STATUS)[1])['usb_service']['replies_lost']
        self.assertEqual((lost['outages'], lost['out_since_ms']), (1, 0))
        self.assertGreater(lost['longest_ms'], 500)
        self.sim.until(lambda: log().rfind('menu: usb line: ') > log().rfind(line), 'the line gone', 2000)

    # -- the spec's stream vectors, cut frames, names ------------------------------------
    def frames(self, sock, parser, until, timeout):
        """Frames from a raw connection until until(frames) holds or timeout."""
        got, deadline = [], time.monotonic() + timeout
        while not until(got) and time.monotonic() < deadline:
            try:
                data = sock.recv(4096)
            except TimeoutError:
                continue
            if not data:
                break
            got += parser.feed(data)
        return got

    @pytest.mark.sim
    def test_cut_frame_is_dropped_after_idle(self):
        """Kept: USB fault: a partial frame is dropped after idle, not glued to the next one. Rule 'resync': a cut frame is dropped after idle_reset_ms of silence (the next hello is
        answered, nothing swallowed); within idle_reset_ms it still waits for its bytes, and the old
        recovery (put_chunk zero bytes and a hello per round, sent without a pause) still works."""
        with socket.create_connection(('127.0.0.1', self.sim.port)) as s:
            s.settimeout(0.05)
            parser = P.Parser()
            s.sendall(P.encode(P.HELLO, 1))
            self.assertEqual(len(self.frames(s, parser, lambda g: g, 2.0)), 1)
            s.sendall(P.encode(P.PUT_DATA, 2, bytes(ids.MAX_PAYLOAD))[:10])  # 4172-byte frame cut after 10
            self.sim.advance(int(2.5 * ids.IDLE_RESET_MS))
            s.sendall(P.encode(P.HELLO, 3))
            got = self.frames(s, parser, lambda g: g, 2.0)
            self.assertEqual([(t, seq) for t, seq, _ in got], [(P.HELLO | P.REPLY, 3)])
            s.sendall(P.encode(P.PUT_DATA, 4, bytes(ids.MAX_PAYLOAD))[:10] + P.encode(P.HELLO, 5))
            self.assertEqual(self.frames(s, parser, lambda g: g, 0.1), [])  # swallowed: within the frame
            for rounds in range(1, 8):
                s.sendall(bytes(ids.PUT_CHUNK) + P.encode(P.HELLO, 10 + rounds))
                if self.frames(s, parser, lambda g: g, 0.05):
                    break
            self.assertLessEqual(rounds, 5)

    @pytest.mark.sim
    def test_storage_full_and_write_failure(self):
        """Kept: Fault: a full storage and a failed write are reported, nothing half-written."""
        dev = self.connect(startup_ready=True)
        data = bytes(10000)
        for fault, value in (('space', 6000), ('fail_write', 2)):  # the second 4 KiB write fails
            self.sim.set(fault, value)
            code, written, _ = self.write(dev, data, BASE + 'log/full.bin')
            self.assertEqual((code, written), (ids.S_STORAGE, 4096))
            self.sim.set('space', -1)
            self.assertEqual(dev.read_file(BASE + 'state-a.txt')[0], 0)  # A back read-only, still serving
        self.assertEqual(self.write(dev, data, BASE + 'log/full.bin')[0], 0)

    @pytest.mark.sim
    def test_power_cut_between_storage_operations(self):
        """Kept: Power cut between storage operations: the next start is clean."""
        dev = self.connect(startup_ready=True)
        data = bytes(range(256)) * 40  # 10 KiB: mkdir, create, 3 writes of 4 KiB at most, close, ...
        path = BASE + 'log/cut/file.bin'
        ops = self.sim.status()['storage_ops']
        self.sim.set('cut_after', 3)  # mkdir log/cut, create file.bin, first write
        with self.assertRaises((OSError, TimeoutError)):
            self.write(dev, data, path)
        status = self.wait(lambda s: s['cuts'] == 1 and s['listening'], what='power cut and restart')
        self.assertEqual((status['storage_ops'], status['power_ons']), (ops + 3, 2))
        self.assertEqual(self.file(path), data[:4096])  # what reached storage before the cut stays
        dev = self.connect()
        self.assertEqual(dev.read_file(BASE + 'state-a.txt')[0], 0)
        self.assertEqual(self.write(dev, data, path)[0], 0)
        self.assertEqual(self.file(path), data)

    # -- file commands --------------------------------------------------------------
    @pytest.mark.sim
    def test_file_list_pages_a_large_folder(self):
        """Kept: A listing larger than one reply pages across the link. A folder of 150 files, a subfolder and a UTF-8 name lists over several pages, each reply
        within read=; kinds and sizes come from A's stat; reads never open the write window."""
        folder = self.dir / 'yaffs2' / 'xy-forever' / 'big'
        folder.mkdir()
        want = {}
        for n in range(150):
            name = 'file-with-a-long-name-%03d.bin' % n
            (folder / name).write_bytes(bytes(n))
            want[name.encode()] = (0, n)
        (folder / 'sub').mkdir()
        want[b'sub'] = (1, 0)
        (folder / 'café.txt').write_bytes(b'12345')
        want['café.txt'.encode()] = (0, 5)
        dev = self.connect()
        pages, skip = 0, 0
        while True:
            code, body = dev.call(P.LIST_DIR, raw_path(BASE + 'big/') + struct.pack('<I', skip))
            self.assertEqual(code, 0)
            self.assertLessEqual(len(body), ids.MAX_READ)
            count, more = struct.unpack_from('<II', body)
            self.assertEqual(len(host.parse_entries(body[8:])), count)
            pages, skip = pages + 1, skip + count
            if not more:
                break
        self.assertGreater(pages, 3)
        code, entries = dev.list_dir(BASE + 'big')
        self.assertEqual((code, {name: (kind, size) for kind, size, name in entries}), (0, want))
        self.assertEqual(len(entries), len(want))
        self.assertEqual(dev.list_dir(BASE + 'missing')[0], ids.S_NOT_FOUND)
        self.assertEqual(dev.list_dir(BASE + 'big/sub'), (0, []))
        self.assertEqual(self.sim.status()['rw_mounts'], 0)

    @pytest.mark.sim
    def test_file_commands_refuse_brick_paths_before_storage(self):
        """Kept: Protected paths: file commands refuse brick paths before storage is touched. With a valid confirmation, the dev-menu marker (any spelling) and /yaffs2 cannot be
        deleted or removed, nothing outside /yaffs2 is listed, made or deleted, and the marker
        cannot be overwritten: refused on B, so not one request reaches A or storage. No command
        renames."""
        root = self.dir / 'yaffs2'
        (root / 'bootloader.dev').write_bytes(b'dev menu\n')
        dev = self.connect(startup_ready=True)
        self.settled(dev)  # next menu turn: deferred startup config read finished before counters
        before = self.sim.status()
        for path in MARKER_SPELLINGS + ROOT_SPELLINGS:
            for kind in (P.DELETE_FILE, P.REMOVE_DIR):
                self.assertEqual(dev.change(kind, path), (ids.S_REFUSED, 0), (path, kind))
        for path in ('/fat32/x', '/yaffs2/../x', '/yaffs2/./bootloader.dev', '/YAFFS2/x', '/yaffs2/a\x01'):
            for kind in (P.DELETE_FILE, P.REMOVE_DIR, P.MAKE_DIR):
                self.assertEqual(dev.change(kind, path), (ids.S_REFUSED, 0), (path, kind))
            self.assertEqual(dev.list_page(path)[0], ids.S_REFUSED, path)
        self.assertEqual(dev.stage(b'not the marker'), 0)
        self.assertEqual(
            dev.file_write('/yaffs2/bootloader.dev', hashlib.sha256(b'not the marker').digest(), 10.0)[0], ids.S_REFUSED
        )
        after = self.sim.status()
        self.assertEqual(
            (after['to_a'], after['storage_ops'], after['rw_mounts']),
            (before['to_a'], before['storage_ops'], before['rw_mounts']),
        )
        self.assertEqual((root / 'bootloader.dev').read_bytes(), b'dev menu\n')
        raw = b'/yaffs2/xy-forever/x\0'
        self.assertEqual(dev.call(P.DELETE_FILE, raw + bytes(32))[0], ids.S_SHA)  # the confirmation binds the path
        self.assertEqual(
            [c['name'] for c in SPEC['commands'] if 'rename' in c['name'] or 'move' in c['name']], ['file.rename']
        )  # test_file_rename_refuses_... below

    # -- file.rename ----------------------------------------------------------------------

    # -- file.copy ------------------------------------------------------------------------

    @pytest.mark.sim
    @pytest.mark.gate  # a simulator UI flow (keys, three minutes of device time): the sim tier has no room
    def test_screen_saver(self):
        """Kept: Screen saver (#315): it starts after a minute of device time with no input; a USB write ends it and
        it starts again a minute after that work; any serial request (a status one) ends it too; a real PLAY wakes it and goes nowhere (no boot), nor does its release; a knob turn
        wakes it too; the next key is the menu's. Screen saver: Off on the Settings page applies at once (it never
        starts again) and is saved."""
        dev = self.connect(startup_ready=True)

        def log():
            return (self.dir / 'log.txt').read_text(errors='replace')

        def key(k, pressed, line):
            seen = log().count(line)
            self.assertTrue(self.sim.control('realkey %d %d' % (k, pressed)).get('ok'))
            self.sim.until(lambda: log().count(line) > seen, line, 2000)

        self.sim.advance(60000)
        # from the connection's last request (2 s past it: activity too)
        self.assertIn('saver: on at ', self.sim.until(lambda: 'saver: on at ' in log() and log(), 'the saver', 5000))
        self.assertIn('(after 60000 ms)', log())
        self.assertEqual(self.write(dev, b'ends the saver', '/yaffs2/saver.txt')[0], 0)  # work: it ends
        self.sim.until(lambda: 'busy after' in log(), 'the saver ended by the write', 1000)
        self.sim.advance(60000)  # from the write's activity line (2 s past it)
        self.sim.until(lambda: log().count('saver: on at ') == 2, 'the saver after the write', 5000)
        status = log().count('busy after')
        self.assertEqual(dev.call(P.STATUS)[0], 0)  # any request, a status one too, ends it
        self.sim.until(lambda: log().count('busy after') > status, 'the saver ended by a status request', 1000)
        self.sim.advance(60000)
        self.sim.until(lambda: log().count('saver: on at ') == 3, 'the saver after the status request', 5000)
        key(51, 1, 'woken by key 51')  # PLAY wakes it: nothing else
        key(51, 0, 'saver: key 51 released: goes nowhere')
        self.sim.settle()
        self.assertNotIn('menu: continue', log())
        self.assertEqual((self.sim.status()['b'], self.sim.status()['listening']), ('running', 1))  # still the menu
        self.sim.advance(60000)
        self.sim.until(lambda: log().count('saver: on at ') == 4, 'the saver again', 1000)
        self.assertTrue(self.sim.control('knob 1 2').get('ok'))  # a knob turn wakes it: moves nothing
        self.sim.until(lambda: 'woken by a knob turn' in log(), 'the knob wake', 2000)
        # Settings (the last home row), Screen saver (its fifth row), - to Off (the last option), picked.
        for k, line in (
            (53, 'menu: screen 0 row 3'),
            (51, 'menu: screen 3 '),
            (54, 'setting 1'),
            (54, 'setting 2'),
            (54, 'setting 3'),
            (54, 'setting 4'),
            (51, 'settings: options current 0'),
            (53, 'menu: screen 4 '),
            (51, 'settings: pick on row 4: option 4'),
        ):
            key(k, 1, line)
            key(k, 0, 'menu: real key %d released' % k)
        self.sim.until(lambda: 'saver=off' in self.file('/yaffs2/xy-forever/settings.txt').decode(), 'the save', 5000)
        self.sim.advance(61000)
        self.assertEqual(log().count('saver: on at '), 4)

    def test_xyf_host_cp(self):
        """Kept: Client smoke test: the xyf_host.py CLI copies a file end to end. xyf-host cp SRC DST --sim: a file; cp -r a folder with a subfolder (mkdir + file.copy per
        file); a folder without -r, a target outside the write area or inside the source refused before
        any copy; an existing target is state."""
        work = self.dir / 'yaffs2' / 'xy-forever' / 'cli'
        (work / 'f' / 'g').mkdir(parents=True)
        (work / 'a.txt').write_bytes(b'a')
        (work / 'f' / 'b.txt').write_bytes(b'bb')
        (work / 'f' / 'g' / 'c.txt').write_bytes(b'ccc')
        self.connect(startup_ready=True).t.close()

        def cp(*argv):
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()), warnings.catch_warnings():
                warnings.simplefilter('ignore', ResourceWarning)  # the tool leaves its port to the process exit
                code = host.main(['--sim', self.sim.address, 'cp'] + list(argv))
                gc.collect()
            return code, out.getvalue()

        code, out = cp(BASE + 'cli/a.txt', BASE + 'cli/z.txt')
        self.assertEqual(code, 0, out)
        self.assertEqual((work / 'z.txt').read_bytes(), b'a')
        code, out = cp(BASE + 'cli/f', BASE + 'cli/h')
        self.assertEqual(code, 2)
        self.assertIn('cp: %scli/f is a folder (cp -r copies folders)\n' % BASE, out)
        code, out = cp('-r', BASE + 'cli/f/', BASE + 'cli/h')
        self.assertEqual(code, 0, out)
        self.assertEqual([(work / 'h' / n).read_bytes() for n in ('b.txt', 'g/c.txt')], [b'bb', b'ccc'])
        self.assertEqual(
            cp('-r', BASE + 'cli/f', BASE + 'cli/f/g/x'),
            (2, 'refused: cp: the target %scli/f/g/x is inside the source %scli/f\n' % (BASE, BASE)),
        )
        self.assertEqual(
            cp(BASE + 'cli/a.txt', '/yaffs2/upgrade/a.txt'),
            (2, 'refused: cp writes only under /yaffs2/, never bootloader.dev or upgrade/ (file.copy)\n'),
        )
        code, out = cp(BASE + 'cli/a.txt', BASE + 'cli/z.txt')
        self.assertEqual(code, ids.S_STATE, out)
        self.assertIn('state (error 17)', out)

    # -- the depth rule (#124) --------------------------------------------------------------
    @pytest.mark.sim
    def test_depth_rule_on_every_write_command(self):
        """Kept: Protected paths: the depth rule holds on every write command. No folder more than 6 below /yaffs2 and no file in one: file.mkdir, file.write_staged, file.copy and
        file.rename are refused at the limit + 1 (nothing reaches A) and done at the limit; a folder moved deeper
        is walked (refused when a folder inside it would sit too deep). Reads, listing and deletes of an older
        deep tree still work. xyf-host says why before it connects, and cp -r checks the whole tree."""
        root = self.dir / 'yaffs2'
        W = BASE + 'wt/'  # 2 folders
        six = W + 'a/b/c/d'  # 6 folders
        (root / 'xy-forever/wt/a/b/c/d/e').mkdir(parents=True)  # e: 7, made before the rule
        (root / 'xy-forever/wt/a/b/c/d/e/old.txt').write_bytes(b'old')
        (root / 'xy-forever/wt/t/u/v').mkdir(parents=True)  # a tree 3 folders tall
        (root / 'xy-forever/wt/t/u/v/x.txt').write_bytes(b'x')
        (root / 'xy-forever/wt/s.txt').write_bytes(b's')
        dev = self.connect(startup_ready=True)
        self.settled(dev)  # next menu turn: deferred startup config read finished before counters
        before = self.sim.status()
        self.assertEqual(dev.change(P.MAKE_DIR, six + '/g'), (ids.S_REFUSED, 0))
        self.assertEqual(dev.copy(W + 's.txt', six + '/e/s.txt')[0], ids.S_REFUSED)
        self.assertEqual(dev.rename(W + 's.txt', six + '/e/s.txt'), (ids.S_REFUSED, 0))
        self.assertEqual(dev.stage(b'abc'), 0)
        code, _ = dev.call(P.FILE_WRITE, (six + '/e/w.txt').encode() + b'\0' + hashlib.sha256(b'abc').digest())
        self.assertEqual(code, ids.S_REFUSED)
        self.assertEqual(self.sim.status()['to_a'], before['to_a'])  # refused on B
        self.assertEqual(host.put(dev, b'abc', six + '/w.txt'), 0)  # at the limit
        self.assertEqual(dev.copy(W + 's.txt', six + '/c.txt')[0], 0)
        self.assertEqual(dev.change(P.MAKE_DIR, W + 'a/b/c/n'), (0, 0))
        self.assertEqual(dev.rename(W + 't/u', W + 'a/u'), (0, 0))  # a/u/v: 5 folders
        self.assertEqual(dev.rename(W + 'a/u', W + 'a/b/u'), (0, 0))  # 6
        self.assertEqual(dev.rename(W + 'a/b/u', W + 'a/b/c/u'), (ids.S_REFUSED, 0))  # v would be 7th
        self.assertEqual(dev.rename(W + 'a/b/u', W + 'u'), (0, 0))  # shallower: no walk
        self.assertEqual((root / 'xy-forever/wt/u/v/x.txt').read_bytes(), b'x')
        self.assertEqual(dev.read_file(six + '/e/old.txt'), (0, b'old'))  # an older deep tree: read, list, delete
        self.assertEqual(dev.list_dir(six + '/e'), (0, [(0, 3, b'old.txt')]))
        self.assertEqual(dev.change(P.DELETE_FILE, six + '/e/old.txt'), (0, 0))
        self.assertEqual(dev.change(P.REMOVE_DIR, six + '/e'), (0, 0))

        def run(*argv):
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()), warnings.catch_warnings():
                warnings.simplefilter('ignore', ResourceWarning)
                code = host.main(['--sim', self.sim.address] + list(argv))
                gc.collect()
            return code, out.getvalue()

        text = 'is more than 6 folders below /yaffs2'
        for argv in (['mkdir', six + '/g'], ['mv', W + 's.txt', six + '/g/s.txt'], ['cp', W + 's.txt', six + '/g/s']):
            code, out = run(*argv)
            self.assertEqual(code, 2, out)
            self.assertIn(text, out)
            self.assertNotIn('connected', out)
        code, out = run('cp', '-r', W + 'u', W + 'a/b/c/u')  # u/v: v would be 7th
        self.assertEqual(code, 2, out)
        self.assertIn('with the folders inside it ' + text, out)
        self.assertFalse((root / 'xy-forever/wt/a/b/c/u').exists())
        code, out = run('cp', '-r', W + 'u', W + 'a/b/u2')
        self.assertEqual(code, 0, out)
        self.assertEqual((root / 'xy-forever/wt/a/b/u2/v/x.txt').read_bytes(), b'x')
        code, out = run('mv', W + 'u', W + 'a/b/c/u')
        self.assertEqual(code, ids.S_REFUSED, out)
        self.assertIn('a folder moved deeper', out)

    def library_boot(self, dev, entry_id, token=None):
        raw = entry_id.encode()
        return dev.call(ids.LIBRARY_BOOT, raw + b'\0' + (token or hashlib.sha256(raw).digest()), timeout=10.0)


@unittest.skipIf(UNAVAILABLE, UNAVAILABLE)
class SimulatorVariantTest(unittest.TestCase):
    def test_sim_address_is_loopback_and_names_both_ports(self):
        for bad in ('10.0.0.2:7341,7342', 'example.com:7341,7342', '127.0.0.1', '127.0.0.1:7341'):
            with self.assertRaises(ValueError):
                host.sim_address(bad)
        self.assertEqual(host.sim_address('127.0.0.1:7341,7342'), ('127.0.0.1', 7341, 7342))


if __name__ == '__main__':
    unittest.main()
