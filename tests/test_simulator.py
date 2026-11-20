"""The device simulator (xy_forever/sim.py, sim/): the real B service, A storage server, file
boot and peer session, built for the Mac, driven over TCP by xyf_host.py's own client.
Covers every command the product build answers, the HELLO, a device pass and each fault hook.
Needs clang++ and the private stock 1.2.1 images (skipped without them). No device access."""
import contextlib
import gc
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import socket
import struct
import sys
import tempfile
import threading
import time
import unittest
import warnings
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import xyf_build                                    # noqa: E402
import xyf_host as host                             # noqa: E402
from xy_forever import census, paths, release, sim, usb_proto as P   # noqa: E402
from xy_forever import usb_proto_ids as ids         # noqa: E402
from xy_forever.startup_ready import wait_startup_ready

UNAVAILABLE = sim.available() or (None if sim.STOCK_OS.is_file() else 'private stock 1.2.1 os.ldr absent')
BASE = '/yaffs2/xy-forever/'
PRE_V1 = 'XYF1;service=usb;role=b;proto=1;install=stock-88a06a7e'
SPEC = json.loads((ROOT / 'protocol' / 'v1.json').read_text())
BUILD = paths.newest_verified_build()                    # a verified build of this source, when present


PARTITION_CLASS, PATH_CLASS, SECTOR_CLASS, WRITE_CLASS = 0x11907694, 0x119076b4, 0x11907348, 0x11907710
READ_CLASS = 0x11907368
EACCES, EBADF, EROFS, EBUSY, EMFILE = 13, 9, 30, 16, 24
COMMAND_TAG = 0x434d                                # peer_services.hpp: a command item's argument tag
INSTALL_ITEM, USB_MODE_ITEM, FILE_WRITE_ITEM = 4, 7, 10   # storage_server.hpp
MARKER_SPELLINGS = ['/yaffs2/bootloader.dev', '/yaffs2//bootloader.dev', '/yaffs2/bootloader.dev/',
                    '/yaffs2/BOOTLOADER.DEV', '/yaffs2/Bootloader.Dev//']
ROOT_SPELLINGS = ['/yaffs2', '/yaffs2/', '/yaffs2//']
RAW_LAYER = [(PARTITION_CLASS, 0x18, 1, b''), (PARTITION_CLASS, 0x14, 0, b''), (PARTITION_CLASS, 4, 0, b''),
             (SECTOR_CLASS, 0x11, 0, b''), (SECTOR_CLASS, 0x12, 1, b''), (WRITE_CLASS, 0x15, 0, bytes(512)),
             (WRITE_CLASS, 0x16, 1, bytes(512))]
# F1: the stock bootloader's boot read (b restarted alone): outside install mode a never answers
# these; it holds the request, closes storage and restarts. The rest of the raw layer is refused.
BOOT_REQUESTS = [(c, o, a, b) for c, o, a, b in RAW_LAYER if (c, o) in ((PARTITION_CLASS, 0x18), (SECTOR_CLASS, 0x11),
                                                                    (SECTOR_CLASS, 0x12))]
REFUSED_RAW = [r for r in RAW_LAYER if r not in BOOT_REQUESTS]


def raw_path(path):
    return path.encode('utf-8') + b'\0'


def status_of(code):
    return P.status_name(code)


@unittest.skipIf(UNAVAILABLE, UNAVAILABLE)
class SimulatorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='xyf-sim-')
        cls.out = sim.build()                            # the shared build cache (by input hash)
        cls.seeded = sim.seed(Path(cls.tmp.name) / 'seed')
        cls.runs = 0

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        type(self).runs += 1
        self.dir = Path(self.tmp.name) / ('run-%d' % self.runs)
        shutil.copytree(self.seeded, self.dir / 'yaffs2')
        self.sim = sim.Simulator(self.dir / 'yaffs2', self.dir / 'bootpart.bin', build_dir=self.out,
                                 log=self.dir / 'log.txt').start()
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
                wait_startup_ready(text, lambda remaining: dev.call(
                    ids.STORAGE_SPACE, struct.pack('<I', 1), timeout=remaining)[0])
        return dev

    def file(self, path):
        return (self.dir / 'yaffs2' / path[len('/yaffs2/'):]).read_bytes()

    def wait(self, predicate, timeout=10.0, what='simulator state'):
        """Until predicate(status). A processor's restart delay (its bootloader's start time) runs on
        the board clock and is passed with advance, not waited for; host time only bounds a hang."""
        deadline = time.monotonic() + max(timeout, 60.0)
        while time.monotonic() < deadline:
            status = self.sim.status()
            if predicate(status):
                return status
            if 'resetting' in (status['a'], status['b']):
                self.sim.advance(100)
            else:
                time.sleep(0.01)
        self.fail('%s not reached within %g s: %s' % (what, timeout, self.sim.status()))

    def restarted(self, boots):
        """Until B booted again after a restart and its endpoint listens (home, USB on)."""
        return self.wait(lambda s: s['b_boots'] > boots and s['listening'], what='B back home')

    def write(self, dev, data, path):
        self.assertEqual(dev.stage(data), 0)
        return dev.file_write(path, hashlib.sha256(data).digest(), timeout=10.0)

    # -- protocol -------------------------------------------------------------------------------
    def test_hello_is_the_generated_text_and_state_comes_first(self):
        dev = self.connect(hello=False)
        for kind in (P.STATUS, P.READ_FILE, P.PUT_BEGIN, P.REBOOT, 0x5f):
            self.assertEqual(dev.call(kind, b'')[0], ids.S_STATE, kind)       # before HELLO: state, nothing done
        code, text = dev.hello()
        self.assertEqual((code, text), (0, P.hello_text(release.VERSION, None, 'product',   # an empty boot partition
                                                   xyf_build.usb_switches('xyf-product'), confirm=0)))   # sim.seed
        self.assertEqual(P.Hello(text).cmds, P.answered(xyf_build.usb_switches('xyf-product')))
        dev.hello_info = None                             # xyf_host refuses types missing from cmds
        self.assertEqual(dev.call(0x5f, b''), (ids.S_UNSUPPORTED, b''))     # not in cmds: status only
        self.assertEqual(self.sim.status()['b_boots'], 1)

    def test_reads_screen_peek_status(self):
        dev = self.connect()
        code, body = dev.call(P.STATUS)
        decoded = host.decode_status(body)
        self.assertEqual((code, decoded['usb_service']['version'], decoded['usb_service']['identity']), (0, 5, '0x0'))
        self.assertEqual(dev.read_file(BASE + 'state-a.txt'), (0, self.file(BASE + 'state-a.txt')))
        os_ldr = BASE + 'firmware/opxy-121/os.ldr'
        data = b''.join(dev.read_file(os_ldr, offset)[1] for offset in range(0, 8192, 1024))
        self.assertEqual(data, self.file(os_ldr)[:8192])
        self.assertEqual(dev.read_file(BASE + 'missing.txt')[0], ids.S_NOT_FOUND)
        self.assertEqual(dev.read_file('/fat32/x')[0], ids.S_BAD_REQUEST)
        self.assertEqual(dev.read_file(os_ldr)[0], ids.S_TOO_LARGE)         # whole form above 4096 B
        code, body = dev.call(P.SCREEN, struct.pack('<I', 0))
        self.assertEqual((code, len(body)), (0, 4 + 1024))
        self.assertEqual(dev.call(P.SCREEN, struct.pack('<I', 0x3c000))[0], ids.S_RANGE)
        code, body = dev.call(P.PEEK, struct.pack('<II', 0x8bfff000, 16))
        self.assertEqual(code, 0)
        self.assertEqual(dev.call(P.PEEK, struct.pack('<II', 0x11a00000, 16))[0], ids.S_RANGE)   # L1 instruction

    def test_staging_and_file_write(self):
        dev = self.connect(startup_ready=True)
        self.assertEqual(dev.call(P.PUT_DATA, struct.pack('<I', 0) + b'x')[0], ids.S_STATE)   # nothing announced
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
        self.assertEqual(dev.call(P.PUT_END)[0], ids.S_STATE)                 # incomplete: discarded

    def test_set_default_mtp_test_keys_hold(self):
        dev = self.connect(startup_ready=True)
        code, _ = dev.set_default('opxy-121-stock')
        self.assertEqual(code, 0)
        best = host.current_state({p: self.file(p) if (self.dir / 'yaffs2' / p[8:]).exists() else None
                                   for p in host.SLOTS})
        self.assertEqual(best[2]['default'], 'opxy-121-stock')
        self.assertEqual(dev.set_default('no-such-entry')[0], ids.S_STORAGE)
        code, body = dev.call(P.MTP_TEST, struct.pack('<I', 100), timeout=10.0)
        values = struct.unpack('<16I', body[:64])
        self.assertEqual((code, values[0], values[13]), (0, 11, 1))           # all 11 steps, unmounted
        self.assertEqual(dev.call(P.KEY, struct.pack('<II', 54, 1))[0], 0)   # + (PLAY on Continue boots)
        self.assertEqual(dev.call(P.KEY, struct.pack('<II', 64, 1))[0], ids.S_BAD_REQUEST)
        self.assertEqual(dev.call(P.HOLD, struct.pack('<I', 1))[0], 0)
        self.assertEqual(host.decode_status(dev.call(P.STATUS)[1])['usb_service']['retired_kicks'], 0)
        info, dev.hello_info = dev.hello_info, None        # xyf_host refuses types missing from cmds
        self.assertEqual(dev.call(0x33, struct.pack('<I', 1))[0], ids.S_UNSUPPORTED)   # diag.guard is gone
        self.assertEqual(dev.call(0x60, b'HANG')[0], ids.S_UNSUPPORTED)                 # diag.hang is gone
        dev.hello_info = info
        self.assertEqual(dev.call(P.HOLD, struct.pack('<I', 7))[0], ids.S_BAD_REQUEST)

    def test_restart_dry_run_and_continue_link(self):
        dev = self.connect()
        self.assertEqual(dev.call(P.HOLD, struct.pack('<I', 1))[0], 0)
        self.assertEqual(dev.call(P.CONTINUE, struct.pack('<I', 2))[0], ids.S_LINK)    # modes >= 2: link
        self.assertEqual(dev.call(P.REBOOT, struct.pack('<I', 3))[0], ids.S_BAD_REQUEST)
        boots = self.sim.status()['b_boots']
        self.assertEqual(dev.call(P.REBOOT, struct.pack('<I', 2))[0], 0)
        self.restarted(boots)
        dev = self.connect()
        usb = host.decode_status(dev.call(P.STATUS)[1])['usb_service']
        self.assertEqual((usb['present'], usb['crash_restarts']), (3 | 1 << 4, 0))      # marker: home
        code, marker = dev.call(P.PEEK, struct.pack('<II', 0x8bfff000, 16))
        magic, inverse, reason, _ = struct.unpack('<4I', marker[4:20])
        self.assertEqual((magic, reason & 0x100), (0x52465859, 0x100))                  # held, carried
        boots = self.sim.status()['b_boots']
        self.assertEqual(dev.call(P.CONTINUE, struct.pack('<I', 1))[0], 0)
        self.restarted(boots)
        dry = host.decode_status(self.connect().call(P.STATUS)[1])['dry_run_file_boot']
        self.assertEqual((dry['outcome'], dry['committed'], dry['image_bytes']), ('file', 1, sim.STOCK_OS.stat().st_size))

    def test_continue_boots_the_default_and_plain_power_on_does_too(self):
        dev = self.connect()
        self.assertEqual(dev.call(P.CONTINUE)[0], 0)
        self.wait(lambda s: s['a'] == 'os' and s['b'] == 'os' and not s['listening'], what='both in the OS')
        self.sim.control('power plain')                                  # no PLAY: straight to the default
        self.wait(lambda s: s['power_ons'] == 2 and s['a'] == 'os' and s['b'] == 'os', what='plain boot')
        self.sim.control('power play')
        self.wait(lambda s: s['listening'], what='menu with USB')


    def boot_image(self, size):
        """A stand-in XY Forever os.ldr of size bytes: the LDR first record (flags 0x5004, argument size - 16) and the
        cold descriptor (magic, version 1); hello image= needs nothing more."""
        head = bytearray(struct.pack('<4I', 0xad005004, 0, 0, size - 16))
        for byte in bytes(head):
            head[2] ^= byte                                # the header check byte: the 16 bytes XOR to 0
        body = bytes(i * 7 & 0xff for i in range(size - 16 - 8)) + struct.pack('<2I', 0x584c4348, 1)
        return bytes(head) + body

    def test_hello_image_is_the_installed_images_hash(self):
        """image= hashes what B's bootloader read (0x8a000000): at power-on and after a restart; the cost is in STATUS."""
        self.sim.stop()
        data = self.boot_image(270 * 1024)                 # about the product's size
        bootpart = bytearray(0x400000 * 2)
        bootpart[:12] = struct.pack('<3I', 3, len(data), zlib.crc32(data))
        bootpart[12] = 1
        bootpart[512:512 + len(data)] = data
        (self.dir / 'bootpart.bin').write_bytes(bytes(bootpart))
        self.sim = sim.Simulator(self.dir / 'yaffs2', self.dir / 'bootpart.bin', build_dir=self.out,
                                 log=self.dir / 'log2.txt').start()
        dev = self.connect(hello=False)
        code, text = dev.hello()
        image = hashlib.sha256(data).hexdigest()[:8]
        self.assertEqual((code, text), (0, P.hello_text(release.VERSION, image, 'product',
                                                       xyf_build.usb_switches('xyf-product'), confirm=0)))
        self.assertEqual(dev.hello(), (0, text))                                # the same text all boot
        code, body = dev.call(P.STATUS)
        built = host.decode_status(body)['usb_service']['build']
        self.assertEqual((built['version'], built['image'], built['state']), (release.VERSION, image, 'known'))
        print('\nimage hash, %d bytes: %d ms menu start to known, %d kcycles hashing (%.1f ms at 400 MHz)' % (
            len(data), built['ms'], built['kcycles'], built['kcycles'] * 1024 / 400e3), file=sys.stderr)
        bad = bytearray(data)
        bad[-8] ^= 1                                       # no cold descriptor: not an XY Forever image
        bootpart[512:512 + len(data)] = bad
        (self.dir / 'bootpart.bin').write_bytes(bytes(bootpart))
        boots = self.sim.status()['b_boots']
        self.assertEqual(dev.call(P.REBOOT, struct.pack('<I', 2))[0], 0)       # home again: the bootloader reads it
        self.restarted(boots)
        dev = self.connect(hello=False)
        self.assertIn(';image=unknown;', dev.hello()[1])

    def test_device_pass_against_the_simulator(self):
        spec = importlib.util.spec_from_file_location('device_pass', ROOT / 'scripts/device_pass.py')
        dp = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(dp)
        argv = ['--sim', self.sim.address] + (['--install', str(BUILD)] if BUILD else [])
        out, err = io.StringIO(), io.StringIO()
        paths.SESSIONS.mkdir(parents=True, exist_ok=True)
        sessions = tempfile.TemporaryDirectory(prefix='simulator-test-', dir=paths.SESSIONS)  # evidence: private only
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                # Its pauses (the write window's settle, screens redrawn) are device time: advanced, not waited.
                code = dp.main(argv, sessions=sessions.name, sleep=lambda s: self.sim.advance(int(s * 1000)))
        finally:
            host.SIM = None
            sessions.cleanup()
        self.assertEqual(code, 0, out.getvalue())
        rows = {line.split()[0]: line.split()[1] for line in out.getvalue().splitlines()[1:-1]}
        self.assertEqual({k for k, v in rows.items() if v == 'SKIP'},
                         {'boot/keys', 'end/keepalive', 'mods/apply',                       # no package set seeded
                          'content/switch', 'config/pending'} |                            # no --sim-log
                         ({'install/install', 'install/install-restart'} if '--install' not in argv else set()))

    # -- the file engine on A, B's checks bypassed ----------------------------------
    def a_request(self, cls, op, argument=0, path=None, body=b'', timeout=3.0):
        """One raw request to A from the board (B never sees it): (result, errno) or None."""
        return self.sim.ask_a(cls, op, argument, 0, raw_path(path) if path is not None else body, timeout)

    def command(self, item):
        """One command item to A from the board (as b's ask(): partition 0x18 with the command tag);
        True once A acknowledged it."""
        type(self).sequence = getattr(type(self), 'sequence', 0) + 1
        value = COMMAND_TAG << 16 | item << 8 | (self.sequence & 0xff)
        reply = self.sim.ask_a(PARTITION_CLASS, 0x18, value)
        return reply is not None and reply[0] & 0xffffffff == ~value & 0xffffffff

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
        returns (thread, reply) once A deletes probe, i.e. the window is open."""
        reply = {}

        def run():
            try:
                reply['r'] = dev.call(P.MTP_TEST, struct.pack('<I', hold_ms), timeout=20.0 + hold_ms / 1000.0)
            except Exception as e:                         # a power cycle ends it
                reply['error'] = e
        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        deadline = time.monotonic() + 5.0
        while self.a_request(PATH_CLASS, 0x25, path=probe) != (0, 0):
            self.assertLess(time.monotonic(), deadline, 'the read-write window never opened')
            time.sleep(0.02)
        return thread, reply

    def test_engine_refuses_brick_paths_on_a_with_b_bypassed(self):
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
            for op in (0x25, 0x27):                        # unlink, rmdir
                self.assertEqual(self.a_request(PATH_CLASS, op, path=path), (-1, EACCES), (path, hex(op)))
        for path in ('/fat32/bootloader.dev', '/yaffs2/a/../bootloader.dev', '/yaffs2/./bootloader.dev',
                     '/YAFFS2/bootloader.dev', '/yaffs2/bootloader.dev\x01'):   # (length: the stock body copy cuts)
            self.assertEqual(self.a_request(PATH_CLASS, 0x25, path=path), (-1, EACCES), path)
        for cls, op, argument, path in ((PARTITION_CLASS, 0x36, 0, None), (PARTITION_CLASS, 0x34, 0, None),
                                        (PATH_CLASS, 0x35, 0, '/yaffs2/probe-2'), (PATH_CLASS, 0x28, 0, '/yaffs2')):
            self.assertEqual(self.a_request(cls, op, argument, path=path), (-1, EACCES), hex(op))   # not exposed
        self.assertEqual(self.a_request(PARTITION_CLASS, 0x2d, 77), (-1, EBADF))      # a handle a never opened
        self.assertEqual(self.a_request(SECTOR_CLASS, 0x2f, 0x1234), (-1, EBADF))     # readdir: refused
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
        self.sim.advance(4000)                 # the hold's end, not waited for (B waits on A's reply)
        thread.join(30.0)
        code, body = reply['r']
        self.assertEqual((code, struct.unpack('<16I', body[:64])[0]), (0, 11))       # the self-test still passes
        self.assertTrue((root / 'bootloader.dev').exists())
        # The raw layer in the run entry is b's bootloader asking (b restarted alone): a never
        # answers or runs it; it closes storage and restarts.
        boots = self.sim.status()['a_boots']
        thread, reply = self.hold_window(dev, 3000, '/yaffs2/probe-2')
        tag = self.sim.inject(PARTITION_CLASS, 0x14, 0)
        self.wait(lambda s: s['a_boots'] > boots, what='A restarted')
        self.assertFalse(self.sim.injected(tag)['done'])
        self.assertEqual(bootpart.read_bytes() if bootpart.exists() else None, before)
        self.assertTrue((root / 'bootloader.dev').exists())
        self.sim.control('power')
        self.wait(lambda s: s['listening'], what='menu with USB after the power cycle')

    def test_boot_requests_are_held_and_a_restarts(self):
        """F1: outside install mode each request of the stock bootloader's boot read (b
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
                self.sim.advance(3000)                         # B's keepalives and patience, on the device clock
                self.wait(lambda s: s['a'] == 'running' and s['a_bootloader'] == 1, what="A's bootloader")
                after = self.sim.status()
                self.assertEqual((after['rw_open'], after['unclean_mounts'], after['boot_writes']), (0, 0, 0))
                self.assertEqual((after['b_boots'], after['b'], after['listening']), (status['b_boots'], 'running', 1))
                self.assertEqual(dev.call(P.READ_FILE, (BASE + 'state-a.txt').encode() + b'\0', timeout=10.0)[0],
                                 ids.S_LINK)
                self.sim.control('power')                      # the way out: a power cycle
                self.wait(lambda s: s['listening'] and s['a'] == 'running' and s['b'] == 'running',
                          what='menu with USB after the power cycle')
                dev = self.connect()
                self.assertEqual(dev.read_file(BASE + 'state-a.txt')[0], 0)
        self.assertFalse(any(bootpart.read_bytes()) if bootpart.exists() else False)
        self.assertIsNone(before)

    def test_no_engine_request_while_a_boot_partition_is_selected(self):
        """The review's sequence: install mode selects boot partition 1, then the file-write
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
        for request in ((PARTITION_CLASS, 8, 0, None), (PATH_CLASS, 0x29, 0x8301, '/yaffs2/new.bin'),
                        (PATH_CLASS, 0x26, 0, '/yaffs2/newdir'), (PATH_CLASS, 0x29, 0, BASE + 'state-a.txt'),
                        (PARTITION_CLASS, 9, 0, None)):
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
    def test_failed_select_counts_as_selected(self):
        """A boot-partition select that fails may still have switched the chip (the hook: the stock
        select switches, then answers 1). A refuses storage from the attempt on, and refuses boot
        writes until a select succeeds; a select of 0 that succeeds ends it."""
        self.connect(startup_ready=True)
        self.assertTrue(self.command(INSTALL_ITEM))
        self.sim.set('fail_select', 1)
        self.assertEqual(self.a_request(PARTITION_CLASS, 0x18, 1), (1, 0))      # failed, but switched
        self.assertEqual(self.sim.status()['partition'], 1)
        for cls, op, argument, path, body in ((PARTITION_CLASS, 8, 0, None, b''),
                                              (PATH_CLASS, 0x29, 0, BASE + 'state-a.txt', b''),
                                              (PARTITION_CLASS, 0x14, 0, None, b''),
                                              (WRITE_CLASS, 0x16, 1, None, bytes(512))):
            self.assertEqual(self.a_request(cls, op, argument, path=path, body=body), (-1, EACCES), hex(op))
        self.assertEqual(self.sim.status()['boot_writes'], 0)
        self.assertEqual(self.a_request(PARTITION_CLASS, 0x18, 0), (0, 0))      # back to the user area
        self.assertEqual(self.a_request(PARTITION_CLASS, 8), (0, 0))
        fd, error = self.a_request(PATH_CLASS, 0x29, 0, path=BASE + 'state-a.txt')
        self.assertGreaterEqual(fd, 0, error)
        self.assertEqual(self.a_request(PARTITION_CLASS, 0x2d, fd), (0, 0))

    def test_unterminated_240_byte_path_end_to_end(self):
        """The stock path buffer holds 239 bytes and the NUL. From the host: a 239-byte path reaches
        A whole; a 240-byte one is refused by B (bad_request: nothing is sent). B bypassed: a
        240-byte path with no NUL in A's buffer (the hook) is refused by A's policy, never run."""
        name = 'p' * (239 - len(BASE))
        (self.dir / 'yaffs2' / 'xy-forever' / name).write_bytes(b'longest name')
        dev = self.connect(startup_ready=True)
        self.assertEqual(dev.call(P.READ_FILE, (BASE + name).encode() + b'\0'), (0, b'longest name'))
        for path in (BASE + name + 'q', BASE + name + 'qq'):
            self.assertEqual(dev.call(P.READ_FILE, path.encode() + b'\0')[0], ids.S_BAD_REQUEST, len(path))
        data = b'x'
        self.assertEqual(dev.stage(data), 0)
        long_write = (BASE + 'w' * (240 - len(BASE))).encode() + b'\0' + hashlib.sha256(data).digest()
        self.assertIn(dev.call(P.FILE_WRITE, long_write)[0], (ids.S_BAD_REQUEST, ids.S_REFUSED))
        self.sim.set('full_path', 1)
        self.assertEqual(self.a_request(PATH_CLASS, 0x29, 0, body=(BASE + name + 'q').encode()), (-1, EACCES))
        self.assertEqual(self.sim.status()['a'], 'running')
        self.assertIn('reached the path buffer whole', (self.dir / 'log.txt').read_text())

    def test_write_area_on_a_with_b_bypassed(self):
        """The write rule (since 1.13): in the write entry A itself
        refuses open-for-write, mkdir, unlink and rmdir of the marker and in /yaffs2/upgrade (EACCES), also through a
        link (reaches_protected), whatever B checked; everything else on /yaffs2 works (user/, the root)."""
        root = self.dir / 'yaffs2'
        (root / 'bootloader.dev').write_bytes(b'dev menu\n')
        (root / 'user/empty').mkdir(parents=True)
        (root / 'user/song.bin').write_bytes(b'mine')
        (root / 'upgrade').mkdir()
        (root / 'upgrade/x.tfw').write_bytes(b'tfw')
        (root / 'inbox-link').symlink_to(root / 'upgrade')
        (root / 'user/marker-link').symlink_to(root / 'bootloader.dev')
        self.connect(startup_ready=True)
        self.write_entry()
        for op, argument, path in ((0x29, 0x8301, '/yaffs2/bootloader.dev'), (0x25, 0, '/yaffs2/bootloader.dev'),
                                   (0x26, 0, '/yaffs2/BOOTLOADER.DEV'), (0x29, 0x8301, '/yaffs2/upgrade/new.tfw'),
                                   (0x29, 0x8301, '/yaffs2/upgrade/x.tfw'), (0x25, 0, '/yaffs2/upgrade/x.tfw'),
                                   (0x26, 0, '/yaffs2/upgrade/dir'), (0x27, 0, '/yaffs2/upgrade'),
                                   (0x29, 0x8301, '/yaffs2/inbox-link/new.tfw'), (0x25, 0, '/yaffs2/inbox-link/x.tfw'),
                                   (0x26, 0, '/yaffs2/inbox-link/dir'), (0x29, 0x8301, '/yaffs2/user/marker-link')):
            self.assertEqual(self.a_request(PATH_CLASS, op, argument, path=path), (-1, EACCES), (hex(op), path))
        self.assertEqual((root / 'bootloader.dev').read_bytes(), b'dev menu\n')
        self.assertEqual(sorted(p.name for p in (root / 'upgrade').iterdir()), ['x.tfw'])
        for op, argument, path in ((0x29, 0x8301, '/yaffs2/user/new.bin'), (0x26, 0, '/yaffs2/user/dir'),
                                   (0x25, 0, '/yaffs2/user/song.bin'), (0x27, 0, '/yaffs2/user/empty'),
                                   (0x26, 0, '/yaffs2/top'), (0x26, 0, BASE + 'bootloader-copy'),
                                   (0x29, 0x8301, '/yaffs2/content/new.bin')):
            value, error = self.a_request(PATH_CLASS, op, argument, path=path)
            self.assertGreaterEqual(value, 0, (hex(op), path, error))
            if op == 0x29:
                self.assertEqual(self.a_request(PARTITION_CLASS, 0x2d, value)[0], 0)
        self.assertTrue((root / 'user/new.bin').exists() and (root / 'top').is_dir() and (root / 'user/dir').is_dir())
        self.assertFalse((root / 'user/song.bin').exists() or (root / 'user/empty').exists())
        self.assertEqual(self.a_request(PATH_CLASS, 0x29, 0, path='/yaffs2/upgrade/x.tfw')[1], 0)   # reading: as before

    def test_failed_marker_restore(self):
        """The marker vanishes with a removal and A's re-create fails (the open hook): A does not
        claim it back, keeps serving, and the next removal does not create one either."""
        root = self.dir / 'yaffs2'
        (root / 'bootloader.dev').write_bytes(b'dev menu\n')
        for name in ('probe-1', 'probe-2'):
            (root / 'xy-forever' / name).write_bytes(b'x')              # in the write area
        self.connect(startup_ready=True)
        self.write_entry()
        self.sim.set('vanish_marker', 1)
        self.sim.set('fail_open', 1)
        self.assertEqual(self.a_request(PATH_CLASS, 0x25, path=BASE + 'probe-1'), (0, 0))   # already answered
        self.assertFalse((root / 'bootloader.dev').exists())
        self.assertIn("fault: A's posix open failed", (self.dir / 'log.txt').read_text())
        self.assertEqual(self.a_request(PATH_CLASS, 0x25, path=BASE + 'probe-2'), (0, 0))
        self.assertFalse((root / 'bootloader.dev').exists() or (root / 'xy-forever/probe-2').exists())
        self.assertEqual(self.a_request(PARTITION_CLASS, 9), (3, 0))   # 3 = closed the window
        self.assertEqual(self.sim.status()['a'], 'running')

    def test_failed_window_close_on_restart_paths(self):
        """The read-write window cannot close (every yaffs unmount fails while the hook is set). A
        REBOOT still restarts A (a reset with the window open is a power cut for yaffs: the next
        mount scans fully, never a hang); a silent B ends A's session without a restart; CONTINUE
        never boots the OS."""
        dev = self.connect(startup_ready=True)
        self.write_entry()
        self.sim.set('fail_unmount', 1)
        self.assertEqual(dev.call(P.CONTINUE)[0], 0)              # accepted; the boot then cannot happen
        self.sim.advance(1000)
        status = self.sim.status()
        self.assertNotEqual(status['a'], 'os')
        self.assertNotEqual(status['b'], 'os')
        self.sim.set('fail_unmount', 0)
        self.sim.control('power')
        self.wait(lambda s: s['listening'], what='menu with USB after the power cycle')
        self.assertEqual(self.sim.status()['unclean_mounts'], 1)
        # REBOOT with the close failing
        dev = self.connect()
        self.write_entry()
        self.sim.set('fail_unmount', 1)
        boots = self.sim.status()
        self.assertEqual(dev.call(P.HOLD, struct.pack('<I', 1))[0], 0)
        self.assertEqual(dev.call(P.REBOOT, struct.pack('<I', 2))[0], 0)
        self.wait(lambda s: s['a_boots'] > boots['a_boots'], what='A restarted')
        self.sim.set('fail_unmount', 0)
        status = self.restarted(boots['b_boots'])
        self.assertEqual(status['unclean_mounts'], 2)
        # B silent past the idle window with the close failing: A's session ends (no restart)
        self.connect()
        self.assertTrue(self.command(USB_MODE_ITEM))
        self.write_entry()
        self.sim.set('fail_unmount', 1)
        boots = self.sim.status()['a_boots']
        self.sim.advance(200)
        self.sim.set('a_silent', 1)
        self.sim.advance(12000)
        status = self.wait(lambda s: s['a'] != 'running', timeout=5.0, what='A out of its session')
        self.assertEqual(status['a_boots'], boots)
        self.sim.set('a_silent', 0)
        self.sim.set('fail_unmount', 0)
        self.sim.control('power')
        self.wait(lambda s: s['listening'], what='menu with USB after the power cycle')
        self.assertEqual(self.sim.status()['unclean_mounts'], 3)

    def test_executor_uses_the_checked_path(self):
        """A checks a copy of the request's path and points the request at it. A second
        request's body arriving in the stock path buffer after the check (the swap hook) does not
        change what the executor does."""
        root = self.dir / 'yaffs2'
        (root / 'bootloader.dev').write_bytes(b'dev menu\n')
        (root / 'xy-forever/probe').write_bytes(b'x')                # in the write area
        self.connect(startup_ready=True)
        self.write_entry()
        self.sim.control('swap /yaffs2/bootloader.dev')
        self.assertEqual(self.a_request(PATH_CLASS, 0x25, path=BASE + 'probe'), (0, 0))
        self.assertFalse((root / 'xy-forever/probe').exists())
        self.assertEqual((root / 'bootloader.dev').read_bytes(), b'dev menu\n')
        self.sim.control('swap /yaffs2/missing')               # a query reads the copy too
        self.assertEqual(self.a_request(0x11907388, 0x22, path='/yaffs2/bootloader.dev'), (0, 0))
        self.assertEqual(self.a_request(PARTITION_CLASS, 9), (3, 0))   # 3 = closed the window

    def test_marker_survives_links_and_vanishing(self):
        """yaffs follows links inside a path, the policy sees only the spelling. A refuses an
        unlink or rmdir whose path resolves to the marker (a symlink to the root here), and puts
        the marker back if it is gone after one (the vanish hook), but never creates one that was
        not there before."""
        root = self.dir / 'yaffs2'
        (root / 'bootloader.dev').write_bytes(b'dev menu\n')
        (root / 'xy-forever/s').symlink_to('..')              # /yaffs2/xy-forever/s -> /yaffs2 (in the area)
        (root / 'xy-forever/m').symlink_to('../bootloader.dev')   # the marker by a name without "bootloader"
        for name in ('probe-1', 'probe-2', 'probe-3'):
            (root / 'xy-forever' / name).write_bytes(b'x')
        self.connect(startup_ready=True)
        self.write_entry()
        for op in (0x25, 0x27):                               # unlink, rmdir
            for path in (BASE + 's/bootloader.dev', BASE + 'm'):
                self.assertEqual(self.a_request(PATH_CLASS, op, path=path), (-1, EACCES), (hex(op), path))
        self.assertEqual((root / 'bootloader.dev').read_bytes(), b'dev menu\n')
        self.assertEqual(self.a_request(PATH_CLASS, 0x25, path=BASE + 's/xy-forever/probe-1'), (0, 0))   # other names
        self.assertFalse((root / 'xy-forever/probe-1').exists())
        self.sim.set('vanish_marker', 1)                      # the marker goes with the next removal
        self.assertEqual(self.a_request(PATH_CLASS, 0x25, path=BASE + 'probe-2'), (0, 0))   # already answered
        self.assertFalse((root / 'xy-forever/probe-2').exists())
        self.assertEqual((root / 'bootloader.dev').read_bytes(), b'')               # re-created
        (root / 'bootloader.dev').unlink()                    # removed by hand (stock dev menu)
        self.sim.set('vanish_marker', 1)
        self.assertEqual(self.a_request(PATH_CLASS, 0x25, path=BASE + 'probe-3'), (0, 0))
        self.assertFalse((root / 'bootloader.dev').exists())  # not created
        (root / 'bootloader.dev').mkdir()                     # a folder by that name also enables the dev menu
        self.assertEqual(self.a_request(PATH_CLASS, 0x27, path=BASE + 's/bootloader.dev'), (-1, EACCES))
        self.assertEqual(self.a_request(PATH_CLASS, 0x27, path=BASE + 'm'), (-1, EACCES))
        self.assertTrue((root / 'bootloader.dev').is_dir())
        self.assertEqual(self.a_request(PARTITION_CLASS, 9), (3, 0))   # 3 = closed the window

    def test_unmount_reports_a_failed_read_only_reopen(self):
        """As build 154: op 9 closes the window (checkpoint) and remounts read-only; when that
        remount fails the reply is the failure (0, EACCES), not success (1)."""
        self.connect(startup_ready=True)
        self.write_entry()
        self.sim.set('fail_mount', 1)                         # the read-only remount after the close
        self.assertEqual(self.a_request(PARTITION_CLASS, 9), (0, EACCES))
        self.assertEqual(self.sim.status()['rw_open'], 0)     # the window itself did close
        self.assertTrue(self.command(FILE_WRITE_ITEM))        # a's storage is gone until a restart
        self.assertEqual(self.a_request(PARTITION_CLASS, 8), (-1, EACCES))
        self.sim.control('power')
        self.wait(lambda s: s['listening'], what='menu with USB after the power cycle')

    def test_write_entry_refuses_brick_paths_with_b_bypassed(self):
        """The file-write item's entry (one read-write window), B's checks bypassed: every
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
        for cls, op, argument, body in REFUSED_RAW:        # refused (boot requests: b's restart, F1)
            self.assertEqual(self.a_request(cls, op, argument, body=body), (-1, EACCES), (hex(cls), hex(op)))
        for cls, op, path in ((PARTITION_CLASS, 0x36, None), (PARTITION_CLASS, 0x34, None),
                              (PATH_CLASS, 0x35, '/yaffs2/probe'), (PATH_CLASS, 0x28, '/yaffs2')):
            self.assertEqual(self.a_request(cls, op, path=path), (-1, EACCES), hex(op))
        self.assertEqual(self.sim.status()['a'], 'running')
        # Allowed: a new file in content/, written and closed; a folder made and one removed; a file
        # deleted. The marker is not overwritten (a name holding "bootloader").
        fd, error = self.a_request(PATH_CLASS, 0x29, 0x8301, path='/yaffs2/content/projects-new.bin')
        self.assertGreaterEqual(fd, 0, error)
        self.assertEqual(self.a_request(WRITE_CLASS, 0x2b, fd, body=b'outside xy-forever'), (18, 0))
        self.assertEqual(self.a_request(PARTITION_CLASS, 0x2d, fd), (0, 0))
        self.assertEqual((root / 'content/projects-new.bin').read_bytes(), b'outside xy-forever')
        self.assertEqual(self.a_request(PATH_CLASS, 0x26, path=BASE + 'newdir'), (0, 0))
        self.assertEqual(self.a_request(PATH_CLASS, 0x27, path=BASE + 'emptydir'), (0, 0))
        self.assertEqual(self.a_request(PATH_CLASS, 0x25, path=BASE + 'probe'), (0, 0))
        self.assertEqual(self.a_request(PATH_CLASS, 0x29, 0x8301, path='/yaffs2/bootloader.dev'), (-1, EACCES))
        self.assertEqual(self.a_request(PARTITION_CLASS, 9), (3, 0))   # 3 = closed the window
        self.assertTrue((root / 'xy-forever/newdir').is_dir())
        self.assertFalse((root / 'xy-forever/emptydir').exists() or (root / 'xy-forever/probe').exists())
        self.assertEqual((root / 'bootloader.dev').read_bytes(), b'dev menu\n')
        self.assertEqual(bootpart.read_bytes() if bootpart.exists() else None, before)
        self.assertEqual(self.sim.status()['rw_open'], 0)

    def test_handle_table_and_read_entry_handles(self):
        """A tracks four files and four folders for b: a fifth of each is refused (EMFILE). Files b
        holds from the read-only entry keep the write entry's window shut (a change is refused EROFS:
        the read-only unmount would fail and end A's storage for this boot) until op 9 closes them."""
        dev = self.connect(startup_ready=True)
        self.assertEqual(dev.read_file(BASE + 'state-a.txt')[0], 0)    # the read-only entry, mounted
        files = [BASE + 'state-a.txt', BASE + 'firmware/opxy-121/info.txt', BASE + 'firmware/opxy-121/os.ldr',
                 BASE + 'firmware/opxy-121-stock/info.txt']
        fds = [self.a_request(PATH_CLASS, 0x29, 0, path=f)[0] for f in files]
        self.assertTrue(all(fd >= 0 for fd in fds), fds)
        self.assertEqual(self.a_request(PATH_CLASS, 0x29, 0, path=BASE + 'firmware/opxy-121-stock/os.ldr'),
                         (-1, EMFILE))
        folders = ['/yaffs2', BASE, BASE + 'firmware', BASE + 'log']
        dirs = [self.a_request(PATH_CLASS, 0x2e, path=f) for f in folders]
        self.assertTrue(all(d[0] not in (0, -1) for d in dirs), dirs)
        self.assertEqual(self.a_request(PATH_CLASS, 0x2e, path=BASE + 'firmware/opxy-121'), (-1, EMFILE))
        self.assertEqual(self.a_request(PARTITION_CLASS, 0x2d, fds[0]), (0, 0))   # one closed: room again
        fds[0], _ = self.a_request(PATH_CLASS, 0x29, 0, path=files[0])
        self.assertGreaterEqual(fds[0], 0)
        # The write entry with b's read-entry files still open: the read-only mount serves them, and
        # the window stays shut (a change is refused read-only) while they are open.
        self.assertTrue(self.command(FILE_WRITE_ITEM))
        self.assertEqual(self.a_request(PARTITION_CLASS, 8), (0, 0))
        self.assertEqual(self.a_request(PATH_CLASS, 0x26, path='/yaffs2/newdir'), (-1, EROFS))
        self.assertEqual(self.sim.status()['rw_open'], 0)
        self.assertEqual(self.sim.ask_a(READ_CLASS, 0x2a, fds[1], 16)[1], 0)          # still served
        self.assertEqual(self.a_request(PARTITION_CLASS, 9), (1, 0))                  # closes b's files and folders
        self.write_entry()                                                            # now the window opens
        self.assertEqual(self.a_request(PARTITION_CLASS, 9), (3, 0))
        self.assertEqual(self.a_request(PARTITION_CLASS, 8), (0, 0))                  # read-only again, serving
        self.assertGreaterEqual(self.a_request(PATH_CLASS, 0x29, 0, path=files[0])[0], 0)

    # every way out of a boot closes the read-write window first (a reset or a boot with it
    # open is a power cut for yaffs: no checkpoint, a full scan at the next mount).
    def test_reboot_closes_the_write_window(self):
        dev = self.connect(startup_ready=True)
        self.write_entry()
        boots = self.sim.status()['b_boots']
        self.assertEqual(dev.call(P.HOLD, struct.pack('<I', 1))[0], 0)
        self.assertEqual(dev.call(P.REBOOT, struct.pack('<I', 2))[0], 0)
        status = self.restarted(boots)
        self.assertEqual((status['a_boots'], status['rw_open'], status['unclean_mounts']), (2, 0, 0))

    def test_continue_closes_the_write_window_before_the_boot(self):
        """The check item closes the window first (checkpoint, read-only again), so the boot
        then hands off a cleanly unmounted yaffs: the OS starts, no unclean mount follows."""
        dev = self.connect(startup_ready=True)
        self.write_entry()
        self.assertEqual(dev.call(P.CONTINUE)[0], 0)
        status = self.wait(lambda s: s['a'] == 'os' and s['b'] == 'os', what='the OS')
        self.assertEqual((status['rw_open'], status['unclean_mounts'], status['boot_writes']), (0, 0, 0))

    def test_session_end_closes_the_write_window(self):
        self.connect(startup_ready=True)
        self.write_entry()
        self.sim.inject(0, 7)                                 # b's release (generic op 7): the session agrees
        status = self.wait(lambda s: s['a'] != 'running', what='A out of its session')
        self.assertEqual((status['rw_open'], status['unclean_mounts']), (0, 0))
        self.sim.control('power')
        self.wait(lambda s: s['listening'], what='menu with USB after the power cycle')

    def test_silent_b_ends_the_session_with_the_write_window_closed(self):
        """B goes silent (nothing reaches A; B's keepalives count, so an idle host never ends the
        session): after the session's idle window (10 s) A's session ends with the read-write window
        closed, and A does not restart. The device clock is advanced (control "advance"), not waited
        for: 8 s silent, A is still in its session (window open); 12 s, its session has ended."""
        self.connect(startup_ready=True)
        self.assertTrue(self.command(USB_MODE_ITEM))
        self.write_entry()
        self.sim.advance(200)                             # A's loop has seen the last request
        self.sim.set('a_silent', 1)
        self.sim.advance(8000)
        status = self.sim.status()
        self.assertEqual((status['a_boots'], status['a'], status['rw_open']), (1, 'running', 1))
        self.sim.advance(4000)
        status = self.wait(lambda s: s['a'] != 'running', timeout=5.0, what='A out of its session')
        self.assertEqual(status['a_boots'], 1)
        self.sim.set('a_silent', 0)
        self.assertEqual((status['rw_open'], status['unclean_mounts']), (0, 0))
        self.sim.control('power')
        self.wait(lambda s: s['listening'], what='menu with USB after the power cycle')

    # -- the write window (A opens it on the first change and keeps it; B closes it once no
    # write came for SETTLE_MS, A on its own after IDLE_MS; every other command item closes it) ---
    SETTLE_MS, IDLE_MS = 8000, 10000                     # storage_server.hpp window_settle_ms / window_idle_ms

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

    def test_write_window_spans_a_burst_and_closes_when_idle(self):
        """Two FILE_WRITEs: one read-write mount; a read in between sees the new data; B closes the
        window (op 9 answers 3: it closed one) after SETTLE_MS on the device clock; A's own idle
        time has not passed yet."""
        dev = self.connect(startup_ready=True)
        mounts = self.sim.status()['rw_mounts']
        first, second = b'first file\n' * 40, b'second file\n' * 90
        self.assertEqual(self.write(dev, first, BASE + 'log/w/a.txt')[0], 0)
        self.assertEqual(self.sim.status()['rw_open'], 1)
        self.assertEqual(dev.read_file(BASE + 'log/w/a.txt'), (0, first))
        self.assertEqual(self.write(dev, second, BASE + 'log/w/b.txt')[0], 0)
        status = self.sim.status()
        self.assertEqual((status['rw_open'], status['rw_mounts'] - mounts), (1, 1))
        self.assertEqual(dev.read_file(BASE + 'log/w/b.txt'), (0, second))
        self.assertEqual(self.b_window(dev), (0, 0, 0))
        self.sim.advance(self.SETTLE_MS - 1500)
        self.assertEqual(self.sim.status()['rw_open'], 1)
        self.sim.advance(2500)
        status = self.wait(lambda s: s['rw_open'] == 0, what='the window closed')
        self.assertEqual((status['rw_mounts'] - mounts, status['unclean_mounts'], status['a_boots']), (1, 0, 1))
        self.assertEqual(self.b_window(dev), (3, 1, 1))
        self.assertEqual((self.file(BASE + 'log/w/a.txt'), self.file(BASE + 'log/w/b.txt')), (first, second))
        self.assertEqual(dev.read_file(BASE + 'log/w/a.txt'), (0, first))       # read-only again, serving

    def test_reads_never_open_the_window(self):
        dev = self.connect()
        for _ in range(3):
            self.assertEqual(dev.read_file(BASE + 'state-a.txt')[0], 0)
            self.assertEqual(dev.read_file(BASE + 'firmware/opxy-121/os.ldr', 1024)[0], 0)
        self.assertTrue(self.command(FILE_WRITE_ITEM))                  # the write entry, reads only
        self.assertEqual(self.a_request(PARTITION_CLASS, 8), (0, 0))
        fd, _ = self.a_request(PATH_CLASS, 0x29, 0, path=BASE + 'state-a.txt')
        self.assertGreaterEqual(fd, 0)
        self.assertEqual(self.a_request(PARTITION_CLASS, 0x2d, fd), (0, 0))
        self.sim.advance(self.IDLE_MS + 1000)
        status = self.sim.status()
        self.assertEqual((status['rw_mounts'], status['rw_open']), (0, 0))
        self.assertEqual(self.a_request(PARTITION_CLASS, 9), (1, 0))   # 1: no window was open

    def test_a_closes_an_idle_window_on_its_own(self):
        """B bypassed (it knows of no write): A closes the window IDLE_MS after its last request,
        stays in the write entry, and the next change opens a new window."""
        self.connect(startup_ready=True)
        self.write_entry()
        self.sim.advance(self.IDLE_MS - 2000)
        self.assertEqual(self.sim.status()['rw_open'], 1)
        self.sim.advance(3000)
        status = self.wait(lambda s: s['rw_open'] == 0, what="A's idle close")
        mounts = status['rw_mounts']
        self.assertEqual(self.a_request(PATH_CLASS, 0x26, path=BASE + 'log')[0], -1)   # a change: open again
        self.assertEqual((self.sim.status()['rw_open'], self.sim.status()['rw_mounts']), (1, mounts + 1))
        self.assertEqual(self.a_request(PARTITION_CLASS, 9), (3, 0))

    def test_library_write_refreshes_the_menu_without_a_restart(self):
        """A new entry's info.txt, then a changed one: after each window closes A scans the library
        again and B's menu catalog shows it (no restart: A and B boots stay 1)."""
        dev = self.connect(startup_ready=True)
        ids_before = [i['id'] for i in self.menu_catalog(dev)['items']]
        self.assertNotIn('xyf-scratch', ids_before)
        info = BASE + 'firmware/xyf-scratch/info.txt'
        for n, name in enumerate(('Scratch one', 'Scratch two'), 1):
            text = ('name=%s\nversion=0.%d\n' % (name, n)).encode()
            self.assertEqual(self.write(dev, text, info)[0], 0)
            self.assertEqual(dev.read_file(info), (0, text))            # the host's read: at once
            self.sim.advance(self.SETTLE_MS + 1000)
            self.wait(lambda s: s['rw_open'] == 0, what='the window closed')
            self.assertEqual(self.b_window(dev), (3, n, n))
            entry = [i for i in self.menu_catalog(dev)['items'] if i['id'] == 'xyf-scratch']
            self.assertEqual([(e['name'], e['version'], e['usable']) for e in entry], [(name, '0.%d' % n, False)])
        status = self.sim.status()
        self.assertEqual((status['a_boots'], status['b_boots'], status['unclean_mounts']), (1, 1, 0))

    def test_window_closes_before_dry_run_install_and_reboot(self):
        dev = self.connect(startup_ready=True)
        # dry run: B closes the window (op 9) before the boot; back home with the result
        self.assertEqual(self.write(dev, b'dry\n', BASE + 'log/w/dry.txt')[0], 0)
        boots = self.sim.status()['b_boots']
        self.assertEqual(dev.call(P.CONTINUE, struct.pack('<I', 1))[0], 0)
        self.restarted(boots)
        dev = self.connect()
        status = self.sim.status()
        self.assertEqual((status['rw_open'], status['unclean_mounts'], status['rw_mounts']), (0, 0, 1))
        dry = host.decode_status(dev.call(P.STATUS)[1])['dry_run_file_boot']
        self.assertEqual(dry['outcome'], 'file')
        # install (here UNINSTALL, the same path): the window closed first, the partition written
        self.assertEqual(self.write(dev, b'install\n', BASE + 'log/w/install.txt')[0], 0)
        self.assertEqual(self.sim.status()['rw_open'], 1)
        stock = sim.STOCK_OS.read_bytes()
        self.assertEqual(dev.stage(stock), 0)
        self.assertEqual(dev.uninstall(hashlib.sha256(stock).digest())[:2], (0, len(stock)))
        status = self.sim.status()
        self.assertEqual((status['rw_open'], status['boot_writes'], status['partition']), (0, 0, 0))
        self.assertEqual(self.b_window(dev)[0], 3)
        # reboot
        self.assertEqual(self.write(dev, b'reboot\n', BASE + 'log/w/reboot.txt')[0], 0)
        boots = self.sim.status()['b_boots']
        self.assertEqual(dev.call(P.REBOOT, struct.pack('<I', 2))[0], 0)
        status = self.restarted(boots)
        self.assertEqual((status['rw_open'], status['unclean_mounts']), (0, 0))
        self.assertEqual(self.file(BASE + 'log/w/reboot.txt'), b'reboot\n')

    def test_window_closes_when_b_goes_silent(self):
        """Nothing from B reaches A (unguarded): A closes the window within IDLE_MS (its idle close,
        or the session's idle window that restarts it closes storage first): never left read-write."""
        dev = self.connect(startup_ready=True)
        self.assertEqual(self.write(dev, b'silent\n', BASE + 'log/w/silent.txt')[0], 0)
        self.sim.advance(200)
        self.sim.set('a_silent', 1)
        self.sim.advance(self.IDLE_MS + 2000)
        status = self.wait(lambda s: s['rw_open'] == 0, timeout=5.0, what='the window closed')
        self.sim.set('a_silent', 0)
        self.sim.control('power')
        status = self.wait(lambda s: s['listening'], what='menu with USB after the power cycle')
        self.assertEqual(status['unclean_mounts'], 0)
        self.assertEqual(self.file(BASE + 'log/w/silent.txt'), b'silent\n')

    def test_failed_window_close_is_reported_and_tried_again(self):
        """Every yaffs unmount fails: B's close gets 0 (status b_window 0), the window stays open;
        once unmounts work again A's own idle close takes it."""
        dev = self.connect(startup_ready=True)
        self.assertEqual(self.write(dev, b'close\n', BASE + 'log/w/close.txt')[0], 0)
        self.sim.set('fail_unmount', 1)
        self.sim.advance(self.SETTLE_MS + 1000)
        self.wait(lambda s: s['rw_open'] == 1, what='settle tried')
        deadline = time.monotonic() + 10
        while self.b_window(dev)[0] != 0 and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertEqual(self.b_window(dev), (0, 0, 1))
        self.assertEqual(self.sim.status()['rw_open'], 1)
        self.sim.set('fail_unmount', 0)
        self.sim.advance(self.IDLE_MS + 1000)
        status = self.wait(lambda s: s['rw_open'] == 0, what="A's idle close")
        self.assertEqual(status['unclean_mounts'], 0)

    def test_power_cut_with_the_window_open(self):
        """A cut while the window is open, after a write completed: no checkpoint (the next mount is
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
                time.sleep(0.05)
        self.fail('storage phase %r not reached: %s' % (phase, st))

    def test_slow_mount_ends_in_a_working_menu(self):
        """A mount longer than every bound B had (command 15 s, blob 15 s, lost 60 s): the menu is up
        and answers USB while A mounts ("Storage: starting..."), then shows the library."""
        dev = self.connect()
        self.sim.set('mount_ms', 600000)                 # A's next read-only mount: until cleared
        dev = self.reboot_home(dev)
        self.assertEqual(self.storage_start(dev)['phase'], 'waiting for A')
        self.sim.advance(90000)               # B's clock past every bound; A still mounting
        self.assertEqual(dev.hello()[0], 0)              # the menu answers meanwhile
        st = self.storage_start(dev)
        self.assertEqual((st['phase'], st['a_mount']), ('waiting for A', None))
        self.assertGreater(st['waited_ms'], 90000)
        self.assertEqual((st['asks'], st['answered_ms']), (1, 0))   # no catalog yet: Continue only
        self.sim.set('mount_ms', 0)                      # the scan ends
        st = self.storage_phase(dev, 'up')
        self.assertEqual((st['a_mount']['result'], st['a_mount']['attempts'], st['waited_ms']), ('ok', 1, 0))
        self.assertGreaterEqual(st['a_mount']['last_s'], 85)
        self.assertTrue(self.menu_catalog(dev)['items'])
        self.assertEqual(dev.read_file(BASE + 'state-a.txt')[0], 0)
        # 193: past 2 s of A's mount the first row says storage is starting and the line why (the clock jumps
        # here: blank first, then "checking"); never a fallback entry name.
        log = (self.dir / 'log.txt').read_text(errors='replace')
        self.assertIn('menu: storage view 4: row: Starting storage...; line: Checking storage after an unclean shutdown', log)
        self.assertNotIn('built-in', log)

    def test_failed_mount_recovers_without_a_power_cycle(self):
        """The boot's read-only mount fails: the menu says so, file commands answer storage (as on the
        device), and B asks again every retry_ms: A mounts again and the library appears."""
        dev = self.connect()
        self.sim.set('fail_mount', 1)                    # the next yaffs mount on A fails
        dev = self.reboot_home(dev)
        st = self.storage_phase(dev, 'A mount failed, asking again')
        self.assertEqual((st['a_mount']['result'], st['a_mount']['attempts']), ('mount', 1))
        self.assertEqual(st['a_mount']['repair'], {'state': 'not needed'})   # 192: only after write_attempt
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
    def repair_state(self, dev, state):
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            st = self.storage_start(dev)
            if st['a_mount'] and st['a_mount']['repair']['state'] == state:
                return st
            time.sleep(0.05)
        self.fail('repair %r not reached: %s' % (state, st))

    def test_clean_mount_writes_nothing(self):
        dev = self.connect()
        before = self.sim.status()
        dev = self.reboot_home(dev)
        st = self.storage_phase(dev, 'up')
        self.assertEqual((st['a_mount']['result'], st['a_mount']['denied_writes'], st['a_mount']['repair']),
                         ('ok', 0, {'state': 'not needed'}))
        after = self.sim.status()
        self.assertEqual((after['rw_mounts'], after['rw_open'], after['boot_writes']),
                         (before['rw_mounts'], 0, before['boot_writes']))   # no read-write mount: no media write

    def test_unclean_shutdown_is_repaired_without_a_power_cycle(self):
        """The read-only mount fails with write_attempt; B shows the repair and asks again at once; A
        repairs (here a slow checkpoint) while the menu answers USB; then the library appears."""
        dev = self.connect()
        rw = self.sim.status()['rw_mounts']
        self.sim.set('needs_repair', 1)
        self.sim.set('unmount_ms', 600000)               # the repair's checkpoint: until cleared
        dev = self.reboot_home(dev)
        self.wait(lambda s: s['rw_open'] == 1, what='the repair mounted read-write')
        st = self.storage_start(dev)
        self.assertEqual(st['phase'], 'A mount failed, asking again')
        self.assertEqual((st['a_mount']['result'], st['a_mount']['attempts'], st['a_mount']['repair']),
                         ('write_attempt', 1, {'state': 'pending'}))
        self.assertGreater(st['a_mount']['denied_writes'], 0)
        self.assertEqual(st['asks'], 2)                  # asked again at once (no retry_ms wait)
        self.assertEqual(dev.hello()[0], 0)              # the menu answers during the repair
        self.sim.set('unmount_ms', 0)                    # the checkpoint ends
        st = self.storage_phase(dev, 'up')
        self.assertEqual((st['a_mount']['result'], st['a_mount']['attempts'], st['a_mount']['repair']['state'],
                          st['a_mount']['repair']['result']), ('ok', 2, 'done', 'ok'))
        status = self.sim.status()
        self.assertEqual((status['needs_repair'], status['rw_open'], status['rw_mounts']), (0, 0, rw + 1))
        self.assertTrue(self.menu_catalog(dev)['items'])
        self.assertEqual(dev.read_file(BASE + 'state-a.txt')[0], 0)
        dev = self.reboot_home(dev)                      # repaired: the next boot is clean
        st = self.storage_phase(dev, 'up')
        self.assertEqual((st['a_mount']['attempts'], st['a_mount']['repair']), (1, {'state': 'not needed'}))

    def test_failed_repair_ends_in_library_unavailable(self):
        """The repair's read-write mount fails: library unavailable with the reason, no further mounts or
        asks this boot, the menu keeps answering."""
        dev = self.connect()
        self.sim.set('needs_repair', 1)
        self.sim.set('fail_mount', 2)                    # the boot's read-only mount, then the repair's fails
        dev = self.reboot_home(dev)
        st = self.repair_state(dev, 'failed')
        self.assertEqual(st['phase'], 'A mount failed, asking again')
        self.assertEqual((st['a_mount']['result'], st['a_mount']['attempts'], st['a_mount']['repair']['result']),
                         ('mount', 2, 'mount'))
        asks = st['asks']
        self.sim.advance(20300)                          # four retry_ms periods
        st = self.storage_start(dev)
        self.assertEqual((st['asks'], st['a_mount']['attempts'], st['a_mount']['repair']['state']), (asks, 2, 'failed'))
        self.assertEqual(self.menu_catalog(dev)['items'], [])
        self.assertEqual(dev.read_file(BASE + 'state-a.txt')[0], ids.S_STORAGE)
        self.assertEqual(dev.hello()[0], 0)
        self.assertEqual(self.sim.status()['needs_repair'], 1)
        self.sim.set('needs_repair', 0)

    def test_power_cut_during_the_repair_repairs_again(self):
        dev = self.connect()
        self.sim.set('needs_repair', 1)
        self.sim.set('unmount_ms', 600000)
        dev = self.reboot_home(dev)
        self.wait(lambda s: s['rw_open'] == 1, what='the repair mounted read-write')
        boots = self.sim.status()['b_boots']
        self.sim.control('cut')                          # before the checkpoint
        self.sim.set('unmount_ms', 0)
        self.assertEqual(self.sim.status()['needs_repair'], 1)
        self.restarted(boots)
        dev = self.connect()
        st = self.storage_phase(dev, 'up')
        self.assertEqual((st['a_mount']['result'], st['a_mount']['attempts'], st['a_mount']['repair']['state']),
                         ('ok', 2, 'done'))
        self.assertEqual(self.sim.status()['needs_repair'], 0)
        self.assertTrue(self.menu_catalog(dev)['items'])

    def settled(self, dev):
        """B closes a read's file after its reply and takes the next command only then: once this
        one is answered the link is idle, and a fault set now hits the next command, not that close."""
        self.assertEqual(dev.call(P.STATUS)[0], 0)

    @staticmethod
    def fault_taken(before, now):
        """A one-shot fault fired, a request was held in B's driver or kept from a silent A."""
        return (any(before[f] > 0 and now[f] == 0 for f in ('drop_request', 'drop_body', 'cut_body'))
                or now['b_queued'] > before['b_queued'] or now['a_silenced'] > before['a_silenced'])

    def call_while_advancing(self, dev, kind, payload, ms, timeout=8.0):
        """dev.call in a thread while the device clocks jump ms ahead (B waits on A's reply)."""
        reply = {}

        def run():
            try:
                reply['r'] = dev.call(kind, payload, timeout=timeout)
            except Exception as e:                         # noqa: BLE001
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
            time.sleep(0.005)
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
        self.assertEqual((status['b'], status['listening'], status['b_boots'], status['b_waits_a']),
                         ('running', 1, boots, 0))
        usb = host.decode_status(dev.call(P.STATUS, timeout=2.0)[1])['usb_service']
        self.assertEqual(usb['crash_restarts'], 0)
        self.assertEqual(dev.call(P.READ_FILE, path, timeout=2.0)[0], ids.S_LINK)
        self.sim.control('power')
        self.wait(lambda s: s['listening'] and s['a'] == 'running' and s['b'] == 'running',
                  what='menu with USB after the power cycle')
        self.assertEqual(self.connect().read_file(BASE + 'state-a.txt')[0], 0)

    def test_a_silent_is_a_link_error_and_usb_stays(self):
        """Before B's timed file client a silent A halted B after ~1 s (keepalives unanswered). Now the file command
        ends with link within its bound and B's menu keeps answering USB."""
        dev = self.connect()
        path = (BASE + 'state-a.txt').encode() + b'\0'
        self.assertEqual(dev.read_file(BASE + 'state-a.txt')[0], 0)     # read mode on, mounted
        self.settled(dev)
        self.sim.set('a_silent', 1)                       # A answers nothing (stock ISR echoes too)
        self.assertEqual(self.call_while_advancing(dev, P.READ_FILE, path, 6000)[0], ids.S_LINK)
        self.sim.set('a_silent', 0)
        self.lost_but_answering(dev)

    def test_a_slow_delays_each_request(self):
        dev = self.connect()
        path = BASE + 'state-a.txt'
        self.assertEqual(dev.read_file(path)[0], 0)
        start = time.monotonic()
        self.assertEqual(dev.read_file(path)[0], 0)
        fast = time.monotonic() - start
        self.sim.set('a_slow_ms', 100)                    # A's loop takes each request 100 ms late
        start = time.monotonic()
        self.assertEqual(dev.read_file(path)[0], 0)
        self.assertGreater(time.monotonic() - start, fast + 0.2)   # open, read, probe, close: >= 3 requests
        self.sim.set('a_slow_ms', 0)

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

    def test_a_slower_than_the_bound_then_a_late_answer(self):
        """A read's data answered after B's 5 s bound: link to the caller; the late body lands in the
        client's own buffer, frees the slot, and is never taken as the next answer: the next read
        (another file) gets its own bytes."""
        dev = self.connect()
        first, other = BASE + 'firmware/opxy-121/os.ldr', BASE + 'firmware/opxy-121-stock/info.txt'
        self.assertEqual(dev.read_file(BASE + 'state-a.txt')[0], 0)
        self.held_then_late(dev, self.ranged(first, 512), nth=3)        # open, seek pass; the read is late
        self.assertEqual(dev.read_file(other, 0, 64), (0, self.file(other)[:64]))
        self.assertNotEqual(self.file(first)[512:576], self.file(other)[:64])
        self.assertEqual(dev.read_file(first, 0, 64), (0, self.file(first)[:64]))
        self.assertEqual(self.sim.status()['b'], 'running')

    def test_held_request_keeps_its_own_path(self):
        """An open left out past its bound points at the client's path buffer, and the
        stock driver reads that buffer only when it sends: the next command must not change it."""
        dev = self.connect()
        first, second = BASE + 'firmware/opxy-121/info.txt', BASE + 'firmware/opxy-121-stock/info.txt'
        self.assertEqual(dev.read_file(BASE + 'state-a.txt')[0], 0)
        self.settled(dev)
        self.sim.set('b_queue', 1)                                      # the open waits in B's driver
        self.assertEqual(self.call_while_advancing(dev, P.READ_FILE, self.ranged(first), 6000)[0], ids.S_LINK)
        self.assertEqual(self.call_while_advancing(dev, P.READ_FILE, self.ranged(second), 1000)[0], ids.S_LINK)
        self.sim.set('b_queue', 0)
        self.wait(lambda s: s['b_queued'] == 0, what='the late answer back in B')
        log = (self.dir / 'log.txt').read_text(errors='replace')
        self.assertIn("B's SPI driver sends a held request: op 29 path " + first + '\n', log)
        self.assertNotIn('held request: op 29 path ' + second, log)
        self.assertEqual(dev.read_file(second, 0, 64), (0, self.file(second)[:64]))

    def test_late_opens_leave_no_handles_on_a(self):
        """Four opens answered after their bound would fill A's handle table (four files
        for B); op 9 before the next open closes them, so the next read works and A's table is free."""
        dev = self.connect()
        files = [BASE + 'state-a.txt', BASE + 'firmware/opxy-121/info.txt', BASE + 'firmware/opxy-121/os.ldr',
                 BASE + 'firmware/opxy-121-stock/info.txt', BASE + 'firmware/opxy-121-stock/os.ldr']
        self.assertEqual(dev.read_file(files[0])[0], 0)
        for k, path in enumerate(files[:4]):                            # after the first: op 9, op 8, then the open
            self.held_then_late(dev, self.ranged(path), nth=3 if k else 1)
        self.assertEqual(dev.read_file(files[4], 0, 64), (0, self.file(files[4])[:64]))
        fds = [self.a_request(PATH_CLASS, 0x29, 0, path=f)[0] for f in files[:4]]
        self.assertTrue(all(fd >= 0 for fd in fds), fds)

    def test_full_handle_table_is_storage_not_not_found(self):
        """An open A refuses with EMFILE is a storage error, not a missing file."""
        dev = self.connect(startup_ready=True)
        files = [BASE + 'state-a.txt', BASE + 'firmware/opxy-121/info.txt', BASE + 'firmware/opxy-121/os.ldr',
                 BASE + 'firmware/opxy-121-stock/info.txt']
        self.assertEqual(dev.read_file(files[0])[0], 0)
        self.assertTrue(all(self.a_request(PATH_CLASS, 0x29, 0, path=f)[0] >= 0 for f in files))
        self.assertEqual(dev.read_file(files[1])[0], ids.S_STORAGE)

    def test_read_after_a_restarted_alone_never_reaches_its_bootloader(self):
        """A restarts alone and its stock bootloader serves the link (no path policy). B
        asks before every file command whether A is still XY Forever: link, and no file request
        reaches the bootloader (before: read mode stayed latched and the open went to it)."""
        dev = self.connect()
        self.assertEqual(dev.read_file(BASE + 'state-a.txt')[0], 0)    # read mode latched, mounted
        self.sim.set('a_boot_ms', 0)
        self.wait(lambda s: s['clock_ms'] - s['b_sent_ms'] < 60, what='a keepalive just answered')
        self.sim.control('reset a')
        self.wait(lambda s: s['a_bootloader'] == 1, what="A's stock bootloader on the link")
        self.assertEqual(dev.call(P.READ_FILE, raw_path(BASE + 'state-a.txt'), timeout=5.0)[0], ids.S_LINK)
        log = (self.dir / 'log.txt').read_text(errors='replace')
        self.assertNotIn("A's bootloader: class %08x" % PATH_CLASS, log)   # (B then takes the lost-link path)

    def test_dropped_request_and_link_error_mid_body_end_as_link(self):
        """Before B's timed file client B waited for good. Now: link within the bound; the request stays out (later
        file commands: link at once) until the lost limit; USB answers throughout."""
        for fault in ('drop_request', 'drop_body'):       # drop_body: the reply, body and all, is lost
            dev = self.connect()
            path = (BASE + 'state-a.txt').encode() + b'\0'
            self.assertEqual(dev.read_file(BASE + 'state-a.txt')[0], 0)
            self.settled(dev)
            self.sim.set(fault, 1)
            payload = path if fault == 'drop_request' else path[:-1] + b'\0' + struct.pack('<II', 0, 64)
            self.assertEqual(self.call_while_advancing(dev, P.READ_FILE, payload, 6000)[0], ids.S_LINK, fault)
            self.assertEqual(self.sim.status()[fault], 0)
            self.assertEqual(dev.call(P.READ_FILE, path, timeout=2.0)[0], ids.S_LINK)   # the slot is still out
            self.assertEqual(dev.call(P.STATUS, timeout=2.0)[0], 0)
            self.lost_but_answering(dev)

    def test_link_cut_mid_body(self):
        """Part of a read's reply body reaches B, then the link drops: link within the bound,
        nothing of the partial body is sent to the host, USB answers until the lost limit."""
        dev = self.connect()
        path = BASE + 'firmware/opxy-121/os.ldr'
        self.assertEqual(dev.read_file(BASE + 'state-a.txt')[0], 0)
        self.settled(dev)
        self.sim.set('cut_body', 100)                                  # 100 of the read's 1024 bytes
        code, body = self.call_while_advancing(dev, P.READ_FILE, self.ranged(path, 0, 1024), 6000)
        self.assertEqual((code, body), (ids.S_LINK, b''))
        self.assertEqual(self.sim.status()['cut_body'], 0)
        self.assertIn('cut after 100 of its 1024 body bytes', (self.dir / 'log.txt').read_text(errors='replace'))
        self.assertEqual(dev.call(P.READ_FILE, raw_path(path), timeout=2.0)[0], ids.S_LINK)
        self.lost_but_answering(dev)

    def test_cable_pulled_mid_frame(self):
        dev = self.connect()
        self.sim.set('cable_pull', 10)                    # the device side: 10 bytes of the reply, then gone
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

    # -- the spec's stream vectors, cut frames, names ------------------------------------
    def frames(self, sock, parser, until, timeout):
        """Frames from a raw connection until until(frames) holds or timeout."""
        got, deadline = [], time.monotonic() + timeout
        while not until(got) and time.monotonic() < deadline:
            try:
                data = sock.recv(4096)
            except socket.timeout:
                continue
            if not data:
                break
            got += parser.feed(data)
        return got

    def test_stream_vectors_against_the_device_code(self):
        """Each to_device stream of the spec, read by read: one reply per frame the spec lists."""
        with socket.create_connection(('127.0.0.1', self.sim.port)) as s:
            s.settimeout(0.05)
            parser = P.Parser()
            for st in (st for st in SPEC['streams'] if st['direction'] == 'to_device'):
                for chunk in st['chunks']:
                    s.sendall(bytes.fromhex(chunk))
                    time.sleep(0.005)                     # separate reads on the device side
                expect = [(int(f['type'], 16) | P.REPLY, f['seq']) for f in st['frames']]
                got = self.frames(s, parser, lambda g: len(g) >= len(expect), 2.0)
                self.assertEqual([(t, seq) for t, seq, _ in got], expect, st['name'])
            s.sendall(P.encode(P.HELLO, 99))              # nothing else was answered
            got = self.frames(s, parser, lambda g: g, 2.0)
            self.assertEqual([(t, seq, body[:4]) for t, seq, body in got], [(P.HELLO | P.REPLY, 99, bytes(4))])

    def test_cut_frame_is_dropped_after_idle(self):
        """Rule 'resync': a cut frame is dropped after idle_reset_ms of silence (the next hello is
        answered, nothing swallowed); within idle_reset_ms it still waits for its bytes, and the old
        recovery (put_chunk zero bytes and a hello per round, sent without a pause) still works."""
        with socket.create_connection(('127.0.0.1', self.sim.port)) as s:
            s.settimeout(0.05)
            parser = P.Parser()
            s.sendall(P.encode(P.HELLO, 1))
            self.assertEqual(len(self.frames(s, parser, lambda g: g, 2.0)), 1)
            s.sendall(P.encode(P.PUT_DATA, 2, bytes(ids.MAX_PAYLOAD))[:10])   # 4172-byte frame cut after 10
            self.sim.advance(int(2.5 * ids.IDLE_RESET_MS))
            s.sendall(P.encode(P.HELLO, 3))
            got = self.frames(s, parser, lambda g: g, 2.0)
            self.assertEqual([(t, seq) for t, seq, _ in got], [(P.HELLO | P.REPLY, 3)])
            s.sendall(P.encode(P.PUT_DATA, 4, bytes(ids.MAX_PAYLOAD))[:10] + P.encode(P.HELLO, 5))
            self.assertEqual(self.frames(s, parser, lambda g: g, 0.1), [])    # swallowed: within the frame
            for rounds in range(1, 8):
                s.sendall(bytes(ids.PUT_CHUNK) + P.encode(P.HELLO, 10 + rounds))
                if self.frames(s, parser, lambda g: g, 0.05):
                    break
            self.assertLessEqual(rounds, 5)

    def test_stage_retries_keep_the_staging(self):
        """A repeated stage.end and a resent stage.data (retries after a lost reply) keep the
        staging; a refused stage.begin forgets it, so no stage.data writes into the old one."""
        dev = self.connect(startup_ready=True)
        data = bytes(range(256)) * 8
        begin = struct.pack('<II', len(data), zlib.crc32(data)) + hashlib.sha256(data).digest()
        self.assertEqual(dev.call(P.PUT_BEGIN, begin)[0], 0)
        self.assertEqual(dev.call(P.PUT_DATA, struct.pack('<I', 0) + data[:1024]), (0, struct.pack('<I', 1024)))
        self.assertEqual(dev.call(P.PUT_DATA, struct.pack('<I', 0) + data[:1024]), (0, struct.pack('<I', 1024)))
        self.assertEqual(dev.call(P.PUT_DATA, struct.pack('<I', 512) + bytes(512))[0], ids.S_RANGE)
        self.assertEqual(dev.call(P.PUT_DATA, struct.pack('<I', 1024) + data[1024:]), (0, struct.pack('<I', 2048)))
        self.assertEqual(dev.call(P.PUT_END)[0], 0)
        self.assertEqual(dev.call(P.PUT_END)[0], 0)
        path = BASE + 'log/retry.bin'
        code, _ = dev.call(P.FILE_WRITE, path.encode() + b'\0' + hashlib.sha256(data).digest(), timeout=10.0)
        self.assertEqual(code, 0)
        self.assertEqual((self.dir / 'yaffs2' / 'xy-forever' / 'log' / 'retry.bin').read_bytes(), data)
        self.assertEqual(dev.call(P.PUT_BEGIN, begin)[0], 0)
        self.assertEqual(dev.call(P.PUT_DATA, struct.pack('<I', 0) + data[:1024])[0], 0)
        self.assertEqual(dev.call(P.PUT_BEGIN, bytes(39))[0], ids.S_BAD_REQUEST)
        self.assertEqual(dev.call(P.PUT_DATA, struct.pack('<I', 0) + data[:1024])[0], ids.S_STATE)
        self.assertEqual(dev.call(P.PUT_END)[0], ids.S_STATE)

    def test_utf8_names_pass_through(self):
        """Today the device checks only the /yaffs2/ start, the parts and the NUL of a path: a UTF-8
        name is written and read back byte for byte (stricter checks: the file engine)."""
        dev = self.connect(startup_ready=True)
        data = b'a UTF-8 name'
        path = (BASE + 'log/caf\u00e9.txt').encode('utf-8')
        self.assertEqual(dev.stage(data), 0)
        code, _ = dev.call(P.FILE_WRITE, path + b'\0' + hashlib.sha256(data).digest(), timeout=10.0)
        self.assertEqual(code, 0)
        self.assertEqual((self.dir / 'yaffs2' / 'xy-forever' / 'log' / 'caf\u00e9.txt').read_bytes(), data)
        self.assertEqual(dev.call(P.READ_FILE, path + b'\0' + struct.pack('<II', 0, 1024)), (0, data))

    def test_storage_full_and_write_failure(self):
        dev = self.connect(startup_ready=True)
        data = bytes(3000)
        for fault, value in (('space', 1500), ('fail_write', 2)):     # the second 1 KiB write fails
            self.sim.set(fault, value)
            code, written, _ = self.write(dev, data, BASE + 'log/full.bin')
            self.assertEqual((code, written), (ids.S_STORAGE, 1024))
            self.sim.set('space', -1)
            self.assertEqual(dev.read_file(BASE + 'state-a.txt')[0], 0)    # A back read-only, still serving
        self.assertEqual(self.write(dev, data, BASE + 'log/full.bin')[0], 0)

    def test_power_cut_between_storage_operations(self):
        dev = self.connect(startup_ready=True)
        data = bytes(range(256)) * 12                      # 3 KiB: mkdir, create, 3 writes, close, ...
        path = BASE + 'log/cut/file.bin'
        ops = self.sim.status()['storage_ops']
        self.sim.set('cut_after', 3)                      # mkdir log/cut, create file.bin, first write
        with self.assertRaises((OSError, TimeoutError)):
            self.write(dev, data, path)
        status = self.wait(lambda s: s['cuts'] == 1 and s['listening'], what='power cut and restart')
        self.assertEqual((status['storage_ops'], status['power_ons']), (ops + 3, 2))
        self.assertEqual(self.file(path), data[:1024])    # what reached storage before the cut stays
        dev = self.connect()
        self.assertEqual(dev.read_file(BASE + 'state-a.txt')[0], 0)
        self.assertEqual(self.write(dev, data, path)[0], 0)
        self.assertEqual(self.file(path), data)

    # -- file commands --------------------------------------------------------------
    def test_file_list_pages_a_large_folder(self):
        """A folder of 150 files, a subfolder and a UTF-8 name lists over several pages, each reply
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
        want['café.txt'.encode('utf-8')] = (0, 5)
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

    def test_file_list_reads_the_stock_readdir_answer(self):
        """A's readdir answers with its dirent's address (stock 889f6c02), never 1, and 0 at the
        end; build 165 took only 1 as an entry and listed every folder empty on the device. A readdir
        A refuses answers -1, which file.list reports as storage, never as an empty folder."""
        folder = self.dir / 'yaffs2' / 'xy-forever' / 'pair'
        folder.mkdir()
        (folder / 'big.bin').write_bytes(bytes(300))
        (folder / 'small.txt').write_bytes(b'x' * 25)
        dev = self.connect()
        code, entries = dev.list_dir(BASE + 'pair')
        self.assertEqual((code, sorted(entries)), (0, [(0, 25, b'small.txt'), (0, 300, b'big.bin')]))
        names = {name for _, _, name in dev.list_dir(BASE.rstrip('/'))[1]}
        self.assertIn(b'firmware', names)
        # The raw answers, from the board (B's mount from file.list is still up).
        handle, error = self.a_request(PATH_CLASS, 0x2e, path=BASE + 'pair')
        self.assertNotIn(handle, (0, -1), error)
        results = []
        while len(results) < 4:
            value, error = self.a_request(SECTOR_CLASS, 0x2f, handle & 0xffffffff)
            results.append(value)
            if not value:
                break
        self.assertEqual(len(results), 3)
        self.assertEqual(results[2], 0)
        for value in results[:2]:
            self.assertNotIn(value, (1, -1))                              # an address
        self.assertEqual(self.a_request(PARTITION_CLASS, 0x30, handle & 0xffffffff), (0, 0))
        self.assertEqual(self.a_request(SECTOR_CLASS, 0x2f, handle & 0xffffffff), (-1, EBADF))   # A refuses

    def test_file_changes_open_the_write_window_and_it_closes(self):
        """mkdir, delete and rmdir go through one read-write window, opened by the first change;
        B closes it SETTLE_MS after the last one (device clock), as after file.write_staged."""
        root = self.dir / 'yaffs2' / 'xy-forever'
        (root / 'log').mkdir(exist_ok=True)
        (root / 'log' / 'old.txt').write_bytes(b'old')
        dev = self.connect(startup_ready=True)
        mounts = self.sim.status()['rw_mounts']
        self.assertEqual(dev.change(P.MAKE_DIR, BASE + 'scratch'), (0, 0))
        self.assertEqual(self.sim.status()['rw_open'], 1)
        self.assertEqual(dev.change(P.MAKE_DIR, BASE + 'scratch'), (ids.S_STORAGE, 17))
        self.assertEqual(dev.change(P.DELETE_FILE, BASE + 'log/old.txt'), (0, 0))
        self.assertEqual(dev.change(P.REMOVE_DIR, BASE + 'log'), (0, 0))
        self.assertEqual(dev.change(P.REMOVE_DIR, BASE + 'firmware'), (ids.S_STATE, 93))
        self.assertEqual(dev.change(P.DELETE_FILE, BASE + 'firmware'), (ids.S_STORAGE, 21))
        self.assertEqual(dev.list_dir(BASE + 'scratch'), (0, []))                # a read inside the window
        self.assertTrue((root / 'scratch').is_dir())
        self.assertFalse((root / 'log').exists())
        self.assertEqual(self.b_window(dev), (0, 0, 0))
        self.sim.advance(self.SETTLE_MS - 1500)
        self.assertEqual(self.sim.status()['rw_open'], 1)
        self.sim.advance(2500)
        status = self.wait(lambda s: s['rw_open'] == 0, what='the window closed')
        self.assertEqual((status['rw_mounts'] - mounts, status['unclean_mounts'], status['a_boots']), (1, 0, 1))
        self.assertEqual(self.b_window(dev), (3, 1, 1))
        self.assertEqual(dev.change(P.REMOVE_DIR, BASE + 'scratch'), (0, 0))   # a new window, closed the same way
        self.sim.advance(self.SETTLE_MS + 1000)
        self.wait(lambda s: s['rw_open'] == 0, what='the second window closed')
        self.assertEqual(self.b_window(dev), (3, 2, 2))

    def test_file_commands_refuse_brick_paths_before_storage(self):
        """With a valid confirmation, the dev-menu marker (any spelling) and /yaffs2 cannot be
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
        self.assertEqual(dev.file_write('/yaffs2/bootloader.dev', hashlib.sha256(b'not the marker').digest(),
                                        10.0)[0], ids.S_REFUSED)
        after = self.sim.status()
        self.assertEqual((after['to_a'], after['storage_ops'], after['rw_mounts']),
                         (before['to_a'], before['storage_ops'], before['rw_mounts']))
        self.assertEqual((root / 'bootloader.dev').read_bytes(), b'dev menu\n')
        raw = b'/yaffs2/xy-forever/x\0'
        self.assertEqual(dev.call(P.DELETE_FILE, raw + bytes(32))[0], ids.S_SHA)   # the confirmation binds the path
        self.assertEqual([c['name'] for c in SPEC['commands'] if 'rename' in c['name'] or 'move' in c['name']],
                         ['file.rename'])                     # test_file_rename_refuses_... below

    # -- file.rename ----------------------------------------------------------------------
    def test_file_rename_moves_files_and_whole_folders(self):
        """A file renamed in its folder, moved to another folder and across the two roots; a folder that
        holds a file and a subfolder renamed as a whole; a UTF-8 name. The target must not exist (state,
        17: nothing replaced); a missing source or target folder is not_found (2); a folder into itself
        storage (22, the stock rename's EINVAL). One write window, closed SETTLE_MS after the last
        change, as for delete and mkdir; A rescans and B fetches the catalog."""
        root = self.dir / 'yaffs2'
        work = root / 'xy-forever' / 'mvt'
        (work / 'tree' / 'sub').mkdir(parents=True)
        (work / 'a.txt').write_bytes(b'alpha')
        (work / 'b.txt').write_bytes(b'beta')
        (work / 'tree' / 'x.bin').write_bytes(bytes(range(256)) * 8)
        (work / 'tree' / 'sub' / 'y.txt').write_bytes(b'y')
        dev = self.connect(startup_ready=True)
        mounts = self.sim.status()['rw_mounts']
        M = BASE + 'mvt/'
        self.assertEqual(dev.rename(M + 'a.txt', M + 'c.txt'), (0, 0))
        self.assertEqual(self.sim.status()['rw_open'], 1)
        self.assertEqual(dev.read_file(M + 'c.txt'), (0, b'alpha'))
        self.assertEqual(dev.read_file(M + 'a.txt')[0], ids.S_NOT_FOUND)
        self.assertEqual(dev.rename(M + 'a.txt', M + 'd.txt'), (ids.S_NOT_FOUND, 2))
        self.assertEqual(dev.rename(M + 'c.txt', M + 'b.txt'), (ids.S_STATE, 17))       # never replaced
        self.assertEqual(dev.rename(M + 'c.txt', M + 'tree'), (ids.S_STATE, 17))
        self.assertEqual((self.file(M + 'b.txt'), self.file(M + 'c.txt')), (b'beta', b'alpha'))
        self.assertEqual(dev.rename(M + 'c.txt', M + 'none/c.txt'), (ids.S_NOT_FOUND, 2))
        self.assertEqual(dev.rename(M + 'c.txt', M + 'tree/sub/c.txt'), (0, 0))            # a move
        self.assertEqual(dev.rename(M + 'tree', M + 'tree2'), (0, 0))                        # a whole folder
        self.assertEqual(dev.rename(M + 'tree2', M + 'tree2/sub/inner'), (ids.S_STORAGE, 22))
        code, entries = dev.list_dir(M + 'tree2')
        self.assertEqual((code, sorted(entries)), (0, [(0, 2048, b'x.bin'), (1, 0, b'sub')]))
        self.assertEqual(sorted(p.name for p in (work / 'tree2' / 'sub').iterdir()), ['c.txt', 'y.txt'])
        self.assertFalse((work / 'tree').exists())
        name = M + 'tree2/sub/\u00e9t\u00e9-\u266b.txt'
        self.assertEqual(dev.rename(M + 'tree2/sub/y.txt', name), (0, 0))
        self.assertEqual(dev.read_file(name), (0, b'y'))
        content = '/yaffs2/content/xyf-mv-test.bin'                                        # across the roots
        self.assertEqual(dev.rename(M + 'tree2/x.bin', content), (0, 0))
        self.assertEqual(self.file(content), bytes(range(256)) * 8)
        self.assertEqual(dev.rename(content, M + 'x.bin'), (0, 0))
        self.assertFalse((root / 'content' / 'xyf-mv-test.bin').exists())
        self.assertEqual(self.b_window(dev), (0, 0, 0))
        self.sim.advance(self.SETTLE_MS - 1500)
        self.assertEqual(self.sim.status()['rw_open'], 1)
        self.sim.advance(2500)
        status = self.wait(lambda s: s['rw_open'] == 0, what='the window closed')
        self.assertEqual((status['rw_mounts'] - mounts, status['unclean_mounts']), (1, 0))
        self.assertEqual(self.b_window(dev), (3, 1, 1))

    def test_file_rename_refuses_brick_paths_and_user_before_storage(self):
        """With a valid confirmation: the marker (any spelling) as source or target, /yaffs2 and the roots
        themselves, user/, other /yaffs2 files, /fat32, a name holding bootloader, '..': refused on B, not
        one request reaches A or storage. A wrong confirmation is sha; a request without the target's NUL
        or a byte short is bad_request."""
        root = self.dir / 'yaffs2'
        (root / 'bootloader.dev').write_bytes(b'dev menu\n')
        (root / 'user').mkdir()
        (root / 'user' / 'project.txt').write_bytes(b'mine')
        (root / 'xy-forever' / 'log' / 'm.txt').write_bytes(b'm')
        dev = self.connect(startup_ready=True)
        self.settled(dev)  # next menu turn: deferred startup config read finished before counters
        mine = BASE + 'log/m.txt'
        refused = MARKER_SPELLINGS + ROOT_SPELLINGS + [               # the write rule: the marker and upgrade/ only
            '/yaffs2/upgrade', '/yaffs2/upgrade/x.tfw', '/yaffs2/UPGRADE/new.txt', '/yaffs2/Upgrade/a/b',
            '/fat32/x', '/yaffs2/xy-forever/', '/yaffs2/content/', '/yaffs2/xy-forever/../user/x',
            '/yaffs2/content/a\x01', '/YAFFS2/xy-forever/m.txt']
        before = self.sim.status()
        for path in refused:
            self.assertEqual(dev.rename(path, BASE + 'log/n.txt'), (ids.S_REFUSED, 0), path)
            self.assertEqual(dev.rename(mine, path), (ids.S_REFUSED, 0), path)
        after = self.sim.status()
        self.assertEqual((after['to_a'], after['storage_ops'], after['rw_mounts']),
                         (before['to_a'], before['storage_ops'], before['rw_mounts']))
        self.assertEqual(((root / 'bootloader.dev').read_bytes(), (root / 'user' / 'project.txt').read_bytes()),
                         (b'dev menu\n', b'mine'))
        pair = mine.encode() + b'\0' + (BASE + 'log/n.txt').encode()
        self.assertEqual(dev.call(P.RENAME_FILE, pair + b'\0' + bytes(32)), (ids.S_SHA, bytes(4)))
        self.assertEqual(dev.call(P.RENAME_FILE, pair + hashlib.sha256(pair).digest())[0], ids.S_BAD_REQUEST)
        self.assertEqual(dev.call(P.RENAME_FILE, pair + b'\0' + hashlib.sha256(pair).digest()[:31])[0],
                         ids.S_BAD_REQUEST)
        self.assertEqual(self.sim.status()['to_a'], before['to_a'])
        self.assertTrue((root / 'xy-forever' / 'log' / 'm.txt').exists())

    def test_xyf_host_mv(self):
        """xyf_host.py mv SRC DST --sim: a file and a folder renamed; a target outside the write area is
        refused by the tool before it connects; an existing target is state."""
        work = self.dir / 'yaffs2' / 'xy-forever' / 'cli'
        (work / 'f').mkdir(parents=True)
        (work / 'a.txt').write_bytes(b'a')
        self.connect(startup_ready=True).t.close()

        def mv(*argv):
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()), warnings.catch_warnings():
                warnings.simplefilter('ignore', ResourceWarning)     # the tool leaves its port to the process exit
                code = host.main(['--sim', self.sim.address, 'mv'] + list(argv))
                gc.collect()
            return code, out.getvalue()
        self.assertEqual(mv(BASE + 'cli/a.txt', BASE + 'cli/f/b.txt')[0], 0)
        code, out = mv(BASE + 'cli/f/', BASE + 'cli/g')
        self.assertEqual(code, 0, out)
        self.assertEqual((work / 'g' / 'b.txt').read_bytes(), b'a')
        self.assertEqual(mv(BASE + 'cli/g', '/yaffs2/upgrade/g'), (2, 'refused: mv moves only under /yaffs2/, never '
                         'bootloader.dev or upgrade/ (file.rename)\n'))
        (work / 'h').mkdir()
        code, out = mv(BASE + 'cli/g', BASE + 'cli/h')
        self.assertEqual(code, ids.S_STATE, out)
        self.assertIn('state (error 17)', out)

    # -- file.copy ------------------------------------------------------------------------
    def test_file_copy_copies_through_b_staging(self):
        """B reads the source into its staging and writes it as file.write_staged does (one write window,
        closed SETTLE_MS after the copy): files of 0 B, 3 KiB and 300 KB, a source outside the write area
        (any file file.read reads). The target must not exist (state, 17: nothing replaced); a missing
        target folder (2) or source is not_found; a target below a file is storage with nothing left. A
        failed write removes its partial target. A staging held before is discarded."""
        root = self.dir / 'yaffs2'
        work = root / 'xy-forever' / 'cpt'
        work.mkdir(parents=True)
        big = bytes(range(251)) * 1196                                     # 300,196 B
        (work / 'a.bin').write_bytes(big)
        (work / 'e.txt').write_bytes(b'')
        (work / 'b.txt').write_bytes(b'beta' * 768)
        dev = self.connect(startup_ready=True)
        self.assertEqual(dev.stage(b'staged before'), 0)
        mounts = self.sim.status()['rw_mounts']
        C = BASE + 'cpt/'
        start = time.monotonic()
        self.assertEqual(dev.copy(C + 'a.bin', C + 'c.bin', len(big)), (0, len(big), 0, 0))
        seconds = time.monotonic() - start
        self.assertEqual(self.file(C + 'c.bin'), big)
        self.assertEqual(self.sim.status()['rw_open'], 1)
        code, _ = dev.call(P.FILE_WRITE, (C + 'x.txt').encode() + b'\0' + hashlib.sha256(b'staged before').digest())
        self.assertEqual(code, ids.S_STATE)                                # the staging held the copy
        self.assertEqual(dev.copy(C + 'b.txt', C + 'd.txt'), (0, 3072, 0, 0))
        self.assertEqual(dev.copy(C + 'e.txt', C + 'f.txt'), (0, 0, 0, 0))
        self.assertEqual(self.file(C + 'f.txt'), b'')
        self.assertEqual(dev.copy('/yaffs2/firmware.version', C + 'fw.txt')[0], 0)
        self.assertEqual(self.file(C + 'fw.txt'), (root / 'firmware.version').read_bytes())
        self.assertEqual(dev.copy(C + 'b.txt', C + 'c.bin'), (ids.S_STATE, 0, 17, 0))       # never replaced
        self.assertEqual(dev.copy(C + 'b.txt', BASE + 'cpt'), (ids.S_STATE, 0, 17, 0))
        self.assertEqual(self.file(C + 'c.bin'), big)
        self.assertEqual(dev.copy(C + 'b.txt', C + 'none/d.txt'), (ids.S_NOT_FOUND, 0, 2, 0))
        self.assertEqual(dev.copy(C + 'none.txt', C + 'g.txt')[0], ids.S_NOT_FOUND)
        self.assertEqual(dev.copy(C + 'b.txt', C + 'b.txt/x.txt'), (ids.S_STORAGE, 3072, 0, 0))
        self.sim.set('fail_write', 2)                                      # the second 1 KiB write fails
        self.assertEqual(dev.copy(C + 'b.txt', C + 'h.txt'), (ids.S_STORAGE, 3072, 0, 0))
        self.assertFalse((work / 'h.txt').exists())                        # the partial target removed
        self.assertEqual(dev.read_file(C + 'd.txt'), (0, b'beta' * 768))
        self.assertEqual(sorted(p.name for p in work.iterdir()), ['a.bin', 'b.txt', 'c.bin', 'd.txt', 'e.txt',
                                                                  'f.txt', 'fw.txt'])
        self.sim.advance(self.SETTLE_MS + 1000)
        status = self.wait(lambda s: s['rw_open'] == 0, what='the window closed')
        self.assertEqual((status['rw_mounts'] - mounts, status['unclean_mounts']), (1, 0))
        print('\nfile.copy 300,196 B in the simulator: %.2f s' % seconds, file=sys.stderr)

    def test_file_copy_refuses_before_a_and_checks_its_request(self):
        """A source file.read refuses or a target file.write_staged refuses is refused on B: not one request
        reaches A. A wrong confirmation is sha; a request without the target's NUL or a byte short is
        bad_request. A source above the staging is too_large, nothing written."""
        root = self.dir / 'yaffs2'
        (root / 'bootloader.dev').write_bytes(b'dev menu\n')
        (root / 'user').mkdir()
        (root / 'user' / 'project.txt').write_bytes(b'mine')
        (root / 'xy-forever' / 'log' / 'm.txt').write_bytes(b'm')
        dev = self.connect(startup_ready=True)
        self.settled(dev)  # next menu turn: deferred startup config read finished before counters
        mine, new = BASE + 'log/m.txt', BASE + 'log/n.txt'
        sources = ROOT_SPELLINGS + ['/fat32/x', '/yaffs2/xy-forever/../user/x', '/YAFFS2/x', '/yaffs2/a\x01']
        targets = MARKER_SPELLINGS + ROOT_SPELLINGS + [               # the write rule: the marker and upgrade/ only
            '/yaffs2/upgrade/x.tfw', '/yaffs2/UPGRADE/new.txt', '/yaffs2/upgrade', '/fat32/x',
            '/yaffs2/xy-forever/', '/yaffs2/content/', '/yaffs2/xy-forever/../user/x', '/yaffs2/content/a\x01']
        before = self.sim.status()
        for path in sources:
            self.assertEqual(dev.copy(path, new), (ids.S_REFUSED, 0, 0, 0), path)
        for path in targets:
            self.assertEqual(dev.copy(mine, path), (ids.S_REFUSED, 0, 0, 0), path)
        pair = mine.encode() + b'\0' + new.encode()
        self.assertEqual(dev.call(P.COPY_FILE, pair + b'\0' + bytes(32)), (ids.S_SHA, bytes(12)))
        self.assertEqual(dev.call(P.COPY_FILE, pair + hashlib.sha256(pair).digest())[0], ids.S_BAD_REQUEST)
        self.assertEqual(dev.call(P.COPY_FILE, pair + b'\0' + hashlib.sha256(pair).digest()[:31])[0],
                         ids.S_BAD_REQUEST)
        after = self.sim.status()
        self.assertEqual((after['to_a'], after['storage_ops'], after['rw_mounts']),
                         (before['to_a'], before['storage_ops'], before['rw_mounts']))
        self.assertEqual(((root / 'bootloader.dev').read_bytes(), (root / 'user' / 'project.txt').read_bytes()),
                         (b'dev menu\n', b'mine'))
        (root / 'xy-forever' / 'log' / 'huge.bin').write_bytes(bytes(host.IMAGE_CAPACITY + 1))
        self.assertEqual(dev.copy(BASE + 'log/huge.bin', new, host.IMAGE_CAPACITY)[:2],
                         (ids.S_TOO_LARGE, host.IMAGE_CAPACITY))
        self.assertFalse((root / 'xy-forever' / 'log' / 'n.txt').exists())

    def test_xyf_host_cp(self):
        """xyf_host.py cp SRC DST --sim: a file; cp -r a folder with a subfolder (mkdir + file.copy per
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
                warnings.simplefilter('ignore', ResourceWarning)     # the tool leaves its port to the process exit
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
        self.assertEqual(cp('-r', BASE + 'cli/f', BASE + 'cli/f/g/x'),
                         (2, 'refused: cp: the target %scli/f/g/x is inside the source %scli/f\n' % (BASE, BASE)))
        self.assertEqual(cp(BASE + 'cli/a.txt', '/yaffs2/upgrade/a.txt'), (2, 'refused: cp writes only under '
                         '/yaffs2/, never bootloader.dev or upgrade/ (file.copy)\n'))
        code, out = cp(BASE + 'cli/a.txt', BASE + 'cli/z.txt')
        self.assertEqual(code, ids.S_STATE, out)
        self.assertIn('state (error 17)', out)

    # -- the depth rule (#124) --------------------------------------------------------------
    def test_depth_rule_on_every_write_command(self):
        """No folder more than 6 below /yaffs2 and no file in one: file.mkdir, file.write_staged, file.copy and
        file.rename are refused at the limit + 1 (nothing reaches A) and done at the limit; a folder moved deeper
        is walked (refused when a folder inside it would sit too deep). Reads, listing and deletes of an older
        deep tree still work. xyf_host.py says why before it connects, and cp -r checks the whole tree."""
        root = self.dir / 'yaffs2'
        W = BASE + 'wt/'                                                  # 2 folders
        six = W + 'a/b/c/d'                                               # 6 folders
        (root / 'xy-forever/wt/a/b/c/d/e').mkdir(parents=True)           # e: 7, made before the rule
        (root / 'xy-forever/wt/a/b/c/d/e/old.txt').write_bytes(b'old')
        (root / 'xy-forever/wt/t/u/v').mkdir(parents=True)               # a tree 3 folders tall
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
        self.assertEqual(self.sim.status()['to_a'], before['to_a'])       # refused on B
        self.assertEqual(host.put(dev, b'abc', six + '/w.txt'), 0)         # at the limit
        self.assertEqual(dev.copy(W + 's.txt', six + '/c.txt')[0], 0)
        self.assertEqual(dev.change(P.MAKE_DIR, W + 'a/b/c/n'), (0, 0))
        self.assertEqual(dev.rename(W + 't/u', W + 'a/u'), (0, 0))       # a/u/v: 5 folders
        self.assertEqual(dev.rename(W + 'a/u', W + 'a/b/u'), (0, 0))     # 6
        self.assertEqual(dev.rename(W + 'a/b/u', W + 'a/b/c/u'), (ids.S_REFUSED, 0))   # v would be 7th
        self.assertEqual(dev.rename(W + 'a/b/u', W + 'u'), (0, 0))       # shallower: no walk
        self.assertEqual((root / 'xy-forever/wt/u/v/x.txt').read_bytes(), b'x')
        self.assertEqual(dev.read_file(six + '/e/old.txt'), (0, b'old'))   # an older deep tree: read, list, delete
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
        code, out = run('cp', '-r', W + 'u', W + 'a/b/c/u')                # u/v: v would be 7th
        self.assertEqual(code, 2, out)
        self.assertIn('with the folders inside it ' + text, out)
        self.assertFalse((root / 'xy-forever/wt/a/b/c/u').exists())
        code, out = run('cp', '-r', W + 'u', W + 'a/b/u2')
        self.assertEqual(code, 0, out)
        self.assertEqual((root / 'xy-forever/wt/a/b/u2/v/x.txt').read_bytes(), b'x')
        code, out = run('mv', W + 'u', W + 'a/b/c/u')
        self.assertEqual(code, ids.S_REFUSED, out)
        self.assertIn('a folder moved deeper', out)

    def test_a_renames_only_a_checked_pair_in_its_window(self):
        """A, from the board (B's checks bypassed): rename_from keeps a source only for the very next
        request; a rename_to without one is refused; the pair's policy and the write area apply on A
        too (the marker, user/, outside: EACCES), a missing source is ENOENT and an existing target
        EEXIST (the stock rename would replace it); only then the yaffs rename (889fa834: 0, or -1 with
        its error number). A rename_to outside the write entry is refused read-only."""
        RNFR, RNTO = 0x52464e52, 0x4f544e52
        root = self.dir / 'yaffs2'
        (root / 'bootloader.dev').write_bytes(b'dev menu\n')
        (root / 'user').mkdir()
        (root / 'xy-forever' / 'log' / 'a.txt').write_bytes(b'a')
        (root / 'xy-forever' / 'log' / 'b.txt').write_bytes(b'b')
        L = BASE + 'log/'
        self.connect(startup_ready=True)
        self.assertTrue(self.command(3))                                     # read-only entry
        self.assertEqual(self.a_request(PARTITION_CLASS, 8), (0, 0))
        self.assertEqual(self.a_request(PATH_CLASS, 0x21, RNFR, path=L + 'a.txt'), (-1, EACCES))   # not writable
        self.write_entry()

        def pair(source, target):
            return self.a_request(PATH_CLASS, 0x21, RNFR, path=source), self.a_request(PATH_CLASS, 0x21, RNTO, path=target)
        self.assertEqual(self.a_request(PATH_CLASS, 0x21, RNTO, path=L + 'c.txt'), (-1, EACCES))   # no source
        self.assertEqual(pair(L + 'a.txt', L + 'c.txt'), ((0, 0), (0, 0)))
        self.assertEqual(self.file(L + 'c.txt'), b'a')
        self.assertEqual(self.a_request(PATH_CLASS, 0x21, RNFR, path=L + 'c.txt'), (0, 0))
        self.assertEqual(self.a_request(PATH_CLASS, 0x21, 4, path=L + 'c.txt'), (0, 0))           # a plain access
        self.assertEqual(self.a_request(PATH_CLASS, 0x21, RNTO, path=L + 'd.txt'), (-1, EACCES))   # disarmed
        self.assertEqual(pair(L + 'c.txt', L + 'b.txt'), ((0, 0), (-1, 17)))                     # EEXIST
        self.assertEqual(pair(L + 'none.txt', L + 'd.txt'), ((0, 0), (-1, 2)))                   # ENOENT
        self.assertEqual(pair(L + 'c.txt', L + 'no/d.txt'), ((0, 0), (-1, 2)))                   # the stock rename's
        for source, target in (('/yaffs2/bootloader.dev', L + 'm'), ('/yaffs2/BOOTLOADER.DEV', L + 'm'),
                               (L + 'c.txt', '/yaffs2/upgrade/c.txt'), (L + 'c.txt', '/yaffs2/bootloader.dev'),
                               ('/yaffs2/upgrade', L + 'u'), (L + 'c.txt', '/yaffs2'), (L + 'c.txt', '/yaffs2/upgrade')):
            first, second = pair(source, target)
            self.assertEqual(second, (-1, EACCES), (source, target))
            self.assertIn(first, ((0, 0), (-1, EACCES)), (source, target))
        self.assertEqual(((root / 'bootloader.dev').read_bytes(), sorted(p.name for p in (root / 'user').iterdir())),
                         (b'dev menu\n', []))
        self.assertEqual(self.file(L + 'c.txt'), b'a')

    def test_a_rename_through_links_with_the_marker_and_inbox_there(self):
        """#144: A stats the marker and the inbox once per request and each path once; a rename whose source or
        target reaches the marker or the inbox through a link (the path itself or a folder above it) is still EACCES,
        rename_over's own checks still answer (EINVAL: size, EISDIR: a folder there), and the renames that pass work
        with both in place."""
        RNFR, RNTO, RPTO = 0x52464e52, 0x4f544e52, 0x4f545052
        # Seed operation inputs after startup's leftover-upload sweep, before direct-A admission.
        self.connect(startup_ready=True)
        root = self.dir / 'yaffs2'
        (root / 'bootloader.dev').write_bytes(b'dev menu\n')
        (root / 'upgrade').mkdir()
        (root / 'upgrade/x.tfw').write_bytes(b'tfw')
        (root / 'user').mkdir()
        (root / 'inbox-link').symlink_to(root / 'upgrade')
        (root / 'user/marker-link').symlink_to(root / 'bootloader.dev')
        (root / 'user/deep').mkdir()
        (root / 'user/deep/up').symlink_to(root / 'upgrade')
        (root / 'xy-forever/log/a.txt').write_bytes(b'a')
        (root / 'xy-forever/tmp').mkdir(exist_ok=True)
        (root / 'xy-forever/tmp/put-00000003-00000001').write_bytes(b'new')
        (root / 'xy-forever/tmp/put-00000009-00000002').write_bytes(b'short')
        (root / 'user/folder').mkdir()
        L, T = BASE + 'log/', BASE + 'tmp/'
        self.write_entry()

        def pair(source, target, tag=RNTO):
            first = self.a_request(PATH_CLASS, 0x21, RNFR, path=source)
            return first, self.a_request(PATH_CLASS, 0x21, tag, path=target)
        for source, target in (('/yaffs2/user/marker-link', L + 'm'), (L + 'a.txt', '/yaffs2/user/marker-link'),
                               (L + 'a.txt', '/yaffs2/inbox-link/a.txt'), ('/yaffs2/inbox-link/x.tfw', L + 'x'),
                               (L + 'a.txt', '/yaffs2/user/deep/up/a.txt'), ('/yaffs2/user/deep/up/x.tfw', L + 'x'),
                               (L + 'a.txt', '/yaffs2/inbox-link')):
            self.assertEqual(pair(source, target)[1], (-1, EACCES), (source, target))
        self.assertEqual(pair(T + 'put-00000003-00000001', '/yaffs2/inbox-link/n.tfw', RPTO)[1], (-1, EACCES))
        self.assertEqual(pair(T + 'put-00000009-00000002', '/yaffs2/user/n.bin', RPTO)[1], (-1, 22))   # EINVAL
        self.assertEqual(pair(T + 'put-00000003-00000001', '/yaffs2/user/folder', RPTO)[1], (-1, 21))  # EISDIR
        self.assertEqual(((root / 'bootloader.dev').read_bytes(), sorted(p.name for p in (root / 'upgrade').iterdir())),
                         (b'dev menu\n', ['x.tfw']))
        self.assertEqual(pair(L + 'a.txt', '/yaffs2/user/deep/a.txt'), ((0, 0), (0, 0)))
        self.assertEqual(pair(T + 'put-00000003-00000001', '/yaffs2/user/deep/a.txt', RPTO), ((0, 0), (0, 0)))
        self.assertEqual(self.file('/yaffs2/user/deep/a.txt'), b'new')
        self.assertTrue((root / 'bootloader.dev').exists())

    def test_content_is_in_the_write_area_and_nothing_else_is_added(self):
        """file.write_staged, mkdir, delete and rmdir work under /yaffs2/content/ (a put replaces a
        file there); every other path outside /yaffs2/xy-forever/ stays refused on B with a valid token
        and staging: /yaffs2/user/, other /yaffs2 files, /fat32, the marker, any name holding
        'bootloader', '..', the roots themselves. Not one refused request reaches A or storage."""
        root = self.dir / 'yaffs2'
        (root / 'user').mkdir()
        (root / 'user' / 'project.txt').write_bytes(b'mine')
        dev = self.connect(startup_ready=True)
        svg, data = '/yaffs2/content/display/xyf-test.svg', b'<svg/>'
        self.assertEqual(self.write(dev, data, svg), (0, len(data), zlib.crc32(data)))       # mkdir -p display/
        self.assertEqual(self.file(svg), data)
        self.assertEqual(self.write(dev, b'<svg>2</svg>', svg)[0], 0)                       # replaced
        self.assertEqual(self.file(svg), b'<svg>2</svg>')
        self.assertEqual(dev.change(P.MAKE_DIR, '/yaffs2/content/presets/xyf-test'), (ids.S_NOT_FOUND, 2))  # no presets/
        self.assertEqual(dev.change(P.MAKE_DIR, '/yaffs2/content/presets'), (0, 0))
        self.assertEqual(dev.change(P.MAKE_DIR, '/yaffs2/content/presets/xyf-test'), (0, 0))
        self.assertEqual(self.write(dev, b'{}', '/yaffs2/content/presets/xyf-test/a.json')[0], 0)
        self.assertEqual(dev.change(P.DELETE_FILE, '/yaffs2/content/presets/xyf-test/a.json'), (0, 0))
        self.assertEqual(dev.change(P.REMOVE_DIR, '/yaffs2/content/presets/xyf-test'), (0, 0))
        self.assertEqual(dev.change(P.DELETE_FILE, svg), (0, 0))
        self.assertFalse((root / 'content' / 'display' / 'xyf-test.svg').exists())
        refused = ['/yaffs2/bootloader.dev', '/yaffs2/BootLoader.dev', '/yaffs2/upgrade', '/yaffs2/upgrade/x.tfw',
                   '/yaffs2/UPGRADE/a/b', '/yaffs2/content/../user/x.txt', '/yaffs2/xy-forever/../user/x',
                   '/yaffs2/content/', '/fat32/x', '/yaffs2/content//a', '/yaffs2/content/a\x01']
        self.assertEqual(dev.stage(b'refused'), 0)
        before = self.sim.status()
        for path in refused:
            self.assertEqual(dev.file_write(path, hashlib.sha256(b'refused').digest(), 10.0)[0], ids.S_REFUSED, path)
            for kind in (P.DELETE_FILE, P.REMOVE_DIR, P.MAKE_DIR):
                self.assertEqual(dev.change(kind, path), (ids.S_REFUSED, 0), (path, kind))
        after = self.sim.status()
        self.assertEqual((after['to_a'], after['storage_ops']), (before['to_a'], before['storage_ops']))
        self.assertEqual((root / 'user' / 'project.txt').read_bytes(), b'mine')
        self.assertFalse((root / 'user' / 'new.txt').exists() or (root / 'x.txt').exists())

    def library_boot(self, dev, entry_id, token=None):
        raw = entry_id.encode()
        return dev.call(ids.LIBRARY_BOOT, raw + b'\0' + (token or hashlib.sha256(raw).digest()), timeout=10.0)

    def test_library_boot_boots_a_named_entry_once(self):
        """library.boot refuses a malformed id, a wrong token, a missing entry (not_found) and one
        without os.ldr (state) without booting; then boots opxy-121-stock (not the default) as PLAY on it
        does: the menu leaves, both processors run the OS; the default and the slots stay."""
        base = self.dir / 'yaffs2' / 'xy-forever'
        slots = {p.name: p.read_bytes() for p in base.glob('state*.txt')}
        dev = self.connect(startup_ready=True)
        self.assertEqual(self.write(dev, b'name=Broken\n', BASE + 'firmware/broken/info.txt')[0], 0)
        self.assertEqual(dev.call(ids.LIBRARY_BOOT, b'BAD!\0' + bytes(32)), (ids.S_BAD_REQUEST, b''))
        self.assertEqual(dev.call(ids.LIBRARY_BOOT, b'opxy-121\0'), (ids.S_BAD_REQUEST, b''))
        self.assertEqual(self.library_boot(dev, 'opxy-121-stock', bytes(32)), (ids.S_SHA, b''))
        self.assertEqual(self.library_boot(dev, 'no-such-entry'), (ids.S_NOT_FOUND, b''))
        self.assertEqual(self.library_boot(dev, 'broken'), (ids.S_STATE, b''))    # the write fetched first
        self.assertEqual(self.library_boot(dev, 'opxy'), (ids.S_NOT_FOUND, b''))   # a prefix is not an id
        self.assertEqual(dev.call(P.STATUS)[0], 0)                                   # still home
        # Without the default's os.ldr only the named entry can reach the OS (the default would fail).
        (base / 'firmware' / 'opxy-121' / 'os.ldr').unlink()
        self.assertEqual(self.library_boot(dev, 'opxy-121-stock'), (0, b''))
        self.wait(lambda s: s['a'] == 'os' and s['b'] == 'os' and not s['listening'], what='both in the OS')
        self.assertRegex((self.dir / 'log.txt').read_text(), r'library\.boot: catalog entry \d once')
        self.assertEqual({p.name: p.read_bytes() for p in base.glob('state*.txt')}, slots)


@unittest.skipIf(UNAVAILABLE, UNAVAILABLE)
class SimulatorVariantTest(unittest.TestCase):
    def test_pre_v1_hello_variant(self):
        with tempfile.TemporaryDirectory(prefix='xyf-sim-') as tmp:
            out = sim.build(hello=PRE_V1)
            root = sim.seed(Path(tmp) / 'yaffs2')
            with sim.Simulator(root, Path(tmp) / 'bootpart.bin', build_dir=out) as running:
                dev = host.Device(host.TcpPort(running.address), on_log=None)
                try:
                    self.assertEqual(dev.hello(), (0, PRE_V1))
                    self.assertTrue(P.Hello(PRE_V1).pre_v1)
                finally:
                    dev.t.close()

    def test_start_moves_off_a_taken_port(self):
        """A port taken between free_port() and the simulator's bind (tests run in parallel): start()
        picks another pair; a port the caller named is never changed."""
        with tempfile.TemporaryDirectory(prefix='xyf-sim-') as tmp, socket.socket() as busy:
            busy.bind(('127.0.0.1', 0))
            busy.listen(1)
            taken = busy.getsockname()[1]
            root = sim.seed(Path(tmp) / 'yaffs2')
            running = sim.Simulator(root, Path(tmp) / 'bootpart.bin', build_dir=sim.build())
            running.port, running.control_port = taken, taken + 1        # as if free_port() had picked it
            with running:
                self.assertNotEqual(running.port, taken)
                self.assertEqual(running.status()['listening'], 1)
            named = sim.Simulator(root, Path(tmp) / 'bootpart.bin', port=taken, build_dir=sim.build())
            with self.assertRaises(sim.PortTaken):
                named.start()
            self.assertIsNone(named.process)

    def test_start_never_takes_another_simulators_ports_for_its_own(self):
        """Two tests' simulators given the same pair: the second's bind fails, but the first answers on the
        control port before the second exits. start() must see that the answer is not its own simulator's
        (status pid), move off, and leave the first running (no "quit" sent to it)."""
        with tempfile.TemporaryDirectory(prefix='xyf-sim-') as tmp:
            first = sim.Simulator(sim.seed(Path(tmp) / 'a'), Path(tmp) / 'a.bin', build_dir=sim.build()).start()
            try:
                self.assertEqual(first.status()['pid'], first.process.pid)
                second = sim.Simulator(sim.seed(Path(tmp) / 'b'), Path(tmp) / 'b.bin', build_dir=sim.build())
                second.port, second.control_port = first.port, first.control_port      # as if free_port() had
                with second:                                                           # picked the same pair
                    self.assertNotEqual(second.port, first.port)
                    self.assertEqual(second.status()['pid'], second.process.pid)
                self.assertIsNone(first.process.poll())
                self.assertEqual(first.status()['listening'], 1)
            finally:
                first.stop()

    def test_sim_address_is_loopback_only(self):
        for bad in ('10.0.0.2:7341', 'example.com:7341', '127.0.0.1'):
            with self.assertRaises(ValueError):
                host.sim_address(bad)
        self.assertEqual(host.sim_address('127.0.0.1:7341'), ('127.0.0.1', 7341))


if __name__ == '__main__':
    unittest.main()
