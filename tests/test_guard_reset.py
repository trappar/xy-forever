"""Lone resets and the guard in the simulator.

The simulator models WDOG0 (each processor's guard), a reset of one processor alone and the stock
bootloaders' side of the boot that follows (sim/boot.cpp). It replaced the guessed restart model
with the stock ordering, read from the 1.2.1 listing and checked on the device:
B's stock reset always completes, but B's bootloader probes A before the display, USB or the link: it
goes on only if A reset at most boot_a_window_ms before B's reset (device: about 0.1 s works, the
boundary lies between 0.15 s and 1.4 s) or resets at any time after it; otherwise B waits with no
bound (off USB, the old frame on screen), shown as b_waits_a. The device cases, each a test below:

1. REBOOT (A answers and resets, B about 0.1 s later): both come back.
2. A restarts alone (160, 162 used diag.restart_a; B then restarted itself, F2, and hung in its
   bootloader). B never resets itself: it stays at the menu with USB, commands answer link.
3. B's guard expiry while A runs XY Forever: B waits until A follows (its idle window, about 10 s
   after B went silent), then both come back (108 recovered so, 25 s later).
4. A B reset where A never resets: B hangs (148-150, 153).
5. A resets long before B (9 s, 135): B hangs.

The expectations are what the model predicts for the current firmware. The firmware removed F2 (B's
own restart on a lost link), the probe, diag.restart_a and A's guard (markers below): a lost link now
ends as link with USB up and no restart.
"""
import shutil
import struct
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import xyf_host as host                             # noqa: E402
from xy_forever import paths, sim, usb_proto as P   # noqa: E402
from xy_forever import usb_proto_ids as ids         # noqa: E402

# No autonomous lone-restart recovery.
F2_REMOVED = True                  # B no longer restarts itself on a lost link (link_lost_restart, F2)
RESTART_A_REMOVED = True           # the test command diag.restart_a is gone
A_GUARD_REMOVED = True             # A's own guard (a_guard, a_guarded) is gone
UNAVAILABLE = sim.available() or (None if sim.STOCK_OS.is_file() else 'private stock 1.2.1 os.ldr absent')
STATE_FILE = '/yaffs2/xy-forever/state-a.txt'
LOST_MS = 60000                                             # xyf_boot.hpp lost_ms
RESTART_A, RSTA = 0x61, b'RSTA'                             # diag.restart_a (removed; unsupported)
# Every time-out in these cases runs on the simulator's clock (control "advance": B's answer patience,
# its probe interval, A's idle window, the guard counts, the bootloaders' start times, which the board
# also counts on that clock). Host time only bounds a hang: a case never needs real seconds to pass.
HOST_CAP = 60.0
FOLLOW_MS = 10000 + 2000           # A follows a silent B: file_boot.cpp product_role0_idle_ms (+ its loop)
FOREVER_MS = 60000                 # "no bound": a minute of device time changes nothing


@unittest.skipIf(UNAVAILABLE, UNAVAILABLE)
class GuardResetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        paths.SESSIONS.mkdir(parents=True, exist_ok=True)
        cls.tmp = Path(tempfile.mkdtemp(prefix='guard-reset-', dir=paths.SESSIONS))
        cls.out = sim.build()
        cls.seeded = sim.seed(cls.tmp / 'seed')
        cls.failed = False

    @classmethod
    def tearDownClass(cls):
        if not cls.failed:                           # evidence kept only when something failed
            shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.dir = self.tmp / self.id().rsplit('.', 1)[-1]
        shutil.copytree(self.seeded, self.dir / 'yaffs2')
        self.sim = sim.Simulator(self.dir / 'yaffs2', self.dir / 'bootpart.bin', build_dir=self.out,
                                 log=self.dir / 'log.txt').start()
        self.links = []

    def tearDown(self):
        outcome = getattr(self, '_outcome', None)
        errors = getattr(outcome, 'errors', None) or getattr(getattr(outcome, 'result', None), 'errors', [])
        if any(error for _, error in errors) or (outcome and not getattr(outcome, 'success', True)):
            type(self).failed = True
        for link in self.links:
            link.t.close()
        self.sim.stop()

    # -- helpers ---------------------------------------------------------------------------
    def connect(self):
        dev = host.Device(host.TcpPort(self.sim.address), on_log=None)
        self.links.append(dev)
        self.assertEqual(dev.hello()[0], 0)
        return dev

    def ok(self, dev, kind, value):
        self.assertEqual(dev.call(kind, struct.pack('<I', value))[0], 0)

    def wait(self, predicate, what, device_ms=0):
        """Until predicate(status). device_ms: the device time the outcome may take, passed on the
        simulator's clock (advance, 300 ms steps, each until the link carried a message or a processor
        changed state) while it is not reached; host time (HOST_CAP) only bounds a hang."""
        deadline, left = time.monotonic() + HOST_CAP, device_ms
        while time.monotonic() < deadline:
            status = self.sim.status()
            if predicate(status):
                return status
            if left > 0:
                self.sim.advance(min(300, left))
                left -= 300
            elif 'resetting' in (status['a'], status['b']):
                self.sim.advance(100, step=None)                   # a bootloader's start time, on the board clock
            else:
                time.sleep(0.01)
        self.fail('%s not reached (device time %d ms): %s' % (what, device_ms, self.sim.status()))

    def settled(self, seconds=0.2):
        """The board's state, unchanged between two looks (no time-out is involved: host time)."""
        last = None
        while True:
            status = self.sim.status()
            key = (status['a'], status['b'], status['a_boots'], status['b_boots'], status['listening'],
                   status['a_bootloader'], status['b_bootloader'])
            if key == last:
                return status
            last = key
            time.sleep(seconds)

    def usb(self, dev):
        return host.decode_status(dev.call(P.STATUS)[1])['usb_service']

    def assert_back_home(self, crash_restarts, b_boots=None):
        """B listens again (after b_boots restarts of B, if given), answers HELLO and STATUS (home, the
        expected crash count) and reads a file; nothing was written to a boot partition, no
        read-write mount is left open."""
        self.wait(lambda s: s['listening'] and s['a'] == 'running' and s['b'] == 'running' and
                  (b_boots is None or s['b_boots'] >= b_boots), 'back on USB')
        dev = self.connect()
        self.assertEqual(self.usb(dev)['crash_restarts'], crash_restarts)
        self.assertEqual(dev.read_file(STATE_FILE)[0], 0)
        self.assertEqual(dev.call(P.STATUS)[0], 0)        # B takes it after the read's close (sent after its reply)
        status = self.sim.status()
        self.assertEqual((status['boot_writes'], status['rw_open'], status['b_waits_a']), (0, 0, 0))
        return dev

    def expiry_test(self, dev):
        """GUARD expiry-test (B's count 3 s, no kicks; A guarded too), on the device clock."""
        expiries = self.sim.status()['b_expiries']
        self.ok(dev, P.GUARD, 2)
        self.wait(lambda s: s['b_expiries'] == expiries + 1, "B's guard expiry", device_ms=6000)

    def restart_a(self, dev):
        """diag.restart_a is gone (unsupported, nothing restarts); A then restarts alone (control
        reset a), and what B does about it (lone_a_outcome)."""
        with self.assertRaises(host.Unsupported):                  # not in HELLO's cmds: the host refuses
            dev.call(RESTART_A, RSTA)
        self.assertNotIn(RESTART_A, ids.COMMANDS)
        self.assertEqual(self.sim.status()['a_boots'], 1)
        self.sim.reset('a')
        return self.lone_a_outcome(dev)

    def lone_a(self, dev):
        """A resets alone (no handler, nothing closed: as a crash), then what B does about it."""
        self.sim.reset('a')
        return self.lone_a_outcome(dev)

    def lone_a_outcome(self, dev):
        """After a lone restart of A (its bootloader waits for B with no bound): B stays at the menu
        with USB, file commands answer link (the former F2 restart hung in case 2)."""
        return self.refused_after(dev)

    def assert_b_hangs(self, a_bootloader):
        """B reset and its bootloader waits for A with no bound: off USB, never back, nothing written.
        a_bootloader: whether A sits in its own bootloader (1) or runs XY Forever (0)."""
        self.wait(lambda s: s['b_waits_a'] == 1, "B's bootloader waiting for A")
        before = self.sim.status()
        self.sim.advance(FOREVER_MS, step=None)
        status = self.settled()
        self.assertEqual((status['b'], status['b_bootloader'], status['b_waits_a'], status['listening'],
                          status['b_boots'], status['a'], status['a_boots'], status['a_bootloader']),
                         ('running', 1, 1, 0, before['b_boots'], 'running', before['a_boots'], a_bootloader))
        self.assertEqual((status['boot_writes'], status['rw_open']), (0, 0))
        return status

    def a_follows(self, a_boots):
        """B waits in its bootloader; A runs XY Forever and follows once B was silent past its idle window
        (A resets: B's bootloader goes on). Returns B's wait (ms of the board clock)."""
        self.wait(lambda s: s['b_waits_a'] == 1 or s['a_boots'] > a_boots, "B's bootloader waiting for A")
        for _ in range(FOLLOW_MS // 300 + 10):
            if self.sim.status()['a_boots'] > a_boots:
                break
            self.sim.advance(300, step=None)                       # nothing to keep alive: B is off the link
            self.a_ran_on(a_boots)
        status = self.wait(lambda s: s['a_boots'] > a_boots, 'A following B')
        return status['a_reset_ms'] - status['b_reset_ms']

    def a_ran_on(self, a_boots, reads=1000):
        """Until A's code has read its clock reads more times since now (its loop ran on the advanced
        clock), A reset or stopped: a starved A is never outrun by the next advance, which would add
        board time A never looked at. Host time only bounds a hang."""
        start = self.sim.status()['a_clock_reads']
        deadline = time.monotonic() + HOST_CAP
        while time.monotonic() < deadline:
            status = self.sim.status()
            if status['a_clock_reads'] - start >= reads or status['a_boots'] > a_boots or status['a'] != 'running':
                return
            time.sleep(0.001)
        self.fail("A's loop did not run on: %s" % self.sim.status())

    def refused_after(self, dev):
        """After a lone reset of A that B must not answer with a restart (B never resets itself).
        A's stock interrupt echoes plain keepalives in every mode and its bootloader answers the file
        client's command check (a select) with no ack, so B's menu may not tell; every file command
        answers link (B asks A before each one). 12 s on (F2 restarted B within ~5 s) and past the
        lost limit, STATUS answers, B has not restarted and stays on USB."""
        boots = self.sim.status()['b_boots']
        self.sim.advance(3000)
        self.wait(lambda s: s['a'] == 'running' and s['a_bootloader'] == 1, "A's bootloader up")
        self.assertEqual(dev.call(P.READ_FILE, STATE_FILE.encode() + b'\0', timeout=10.0)[0], ids.S_LINK)
        self.assertEqual(dev.call(P.STATUS)[0], 0)
        self.sim.advance(12000)                                    # B's menu loop runs on: still no restart
        self.sim.advance(LOST_MS, step=None)                       # and past the lost limit
        status = self.settled()
        self.assertEqual((status['b_boots'], status['b'], status['listening'], status['a_bootloader'],
                          status['b_waits_a']), (boots, 'running', 1, 1, 0))
        self.assertEqual(dev.call(P.STATUS)[0], 0)
        self.assertEqual(dev.call(P.READ_FILE, STATE_FILE.encode() + b'\0', timeout=10.0)[0], ids.S_LINK)
        return status

    # -- the five device cases ----------------------------------------------------
    def test_case1_reboot_both_come_back(self):
        """REBOOT: A answers and resets, B resets about 0.1 s later, inside the window: B's bootloader
        finds A fresh and both come back (device: about 2 s), crash count 0."""
        dev = self.connect()
        self.assertEqual(dev.call(P.REBOOT, struct.pack('<I', 2))[0], 0)
        status = self.wait(lambda s: s['a_boots'] == 2 and s['b_boots'] == 2 and s['listening'], 'home after the REBOOT')
        self.assertLessEqual(0, status['b_reset_ms'] - status['a_reset_ms'])
        self.assertLessEqual(status['b_reset_ms'] - status['a_reset_ms'], status['boot_a_window_ms'])
        self.assertEqual(status['b_waits_a'], 0)
        dev = self.assert_back_home(0)
        self.assertNotIn('B waits for A', (self.dir / 'log.txt').read_text(errors='replace'))

    def test_case2_a_restarts_alone_b_stays_at_the_menu(self):
        """160, 162 (diag.restart_a, now unsupported): A restarts alone and its bootloader waits. B
        never restarts itself (the former F2 restart hung): the menu stays with USB, file
        commands answer link, the crash count stays 0, B's bootloader never runs."""
        dev = self.connect()
        self.assertEqual(dev.read_file(STATE_FILE)[0], 0)
        status = self.restart_a(dev)
        self.assertEqual((status['a_boots'], status['b_boots'], status['b_bootloader'], status['b_waits_a']),
                         (2, 1, 0, 0))
        self.assertEqual(self.usb(dev)['crash_restarts'], 0)

    def test_case3_b_guard_expiry_a_follows(self):
        """153's sequence (guard on, a file read, guard off, expiry-test): B's guard resets B while A runs
        XY Forever. B's bootloader waits; A, B silent past its idle window (about 10 s), follows; B gets
        through and both come home (crash 1). A's XY Forever never answers B's bootloader."""
        dev = self.connect()
        self.ok(dev, P.GUARD, 1)
        self.assertEqual(dev.read_file(STATE_FILE)[0], 0)          # A in RPC mode (command item)
        self.ok(dev, P.GUARD, 0)                                    # device_pass: guard off first
        self.expiry_test(dev)
        waited = self.a_follows(1)
        self.assertGreater(waited, 9000)
        self.assertLessEqual(waited, FOLLOW_MS + 1000)
        self.assert_back_home(1)
        status = self.sim.status()
        self.assertEqual((status['a_boots'], status['b_boots']), (2, 2))   # A followed B, once each
        log = (self.dir / 'log.txt').read_text(errors='replace')
        self.assertIn('B waits for A', log)
        self.assertNotIn('select 1 answered', log)
        self.assertNotIn('select refused', log)

    def test_case4_b_reset_a_never_resets_b_hangs(self):
        """A never resets (here A's loop hangs with its guard off, so it never follows; 148-150 and 153
        had no follow at all): a lone reset of B waits in B's bootloader for good."""
        dev = self.connect()
        self.assertEqual(dev.read_file(STATE_FILE)[0], 0)
        self.sim.set('a_stall_now', 0x7fffffff)                    # A's loop stops (the link still echoes)
        self.sim.advance(300, step=None)
        self.sim.reset('b')
        self.assert_b_hangs(a_bootloader=0)

    def test_case5_a_resets_long_before_b_b_hangs(self):
        """135: A resets alone and B resets 9 s later (here after an uninstall; B does not restart
        itself, then control reset b): B's bootloader meets A's running bootloader and waits for good."""
        import hashlib
        dev = self.connect()
        stock = sim.STOCK_OS.read_bytes()
        self.assertEqual(dev.stage(stock), 0)
        self.assertEqual(dev.uninstall(hashlib.sha256(stock).digest())[0], 0)
        self.sim.reset('a')
        self.refused_after(dev)
        status = self.sim.status()
        self.sim.advance(max(100, 9000 - (status['clock_ms'] - status['a_reset_ms'])), step=None)
        self.sim.reset('b')
        status = self.assert_b_hangs(a_bootloader=1)
        self.assertGreaterEqual(status['b_reset_ms'] - status['a_reset_ms'], 9000)

    # -- 154, 108 ------------------------------------------------------------------------------------
    def test_154_guard_off_then_status_polls_only(self):
        dev = self.connect()
        self.ok(dev, P.GUARD, 1)
        self.assertEqual(dev.read_file(STATE_FILE)[0], 0)          # the last request that reaches A
        last_a = self.sim.status()['clock_ms']
        self.ok(dev, P.GUARD, 0)
        self.ok(dev, P.HOLD, 0)
        while self.sim.status()['clock_ms'] - last_a < 32000:      # only STATUS (B answers it alone)
            self.assertEqual(dev.call(P.STATUS)[0], 0)
            self.sim.advance(300)
        status = self.settled()
        self.assertEqual((status['a_boots'], status['b_boots']), (1, 1))   # nothing restarted
        self.sim.expire('a')                                        # A has no guard: nothing happens
        self.sim.expire('b')
        self.sim.advance(300)                                       # both loops ran (the flags are consumed)
        self.assertEqual((self.sim.status()['a_expiries'], self.sim.status()['b_expiries']), (0, 0))
        self.assertEqual(dev.read_file(STATE_FILE)[0], 0)
        self.assertEqual(self.usb(dev)['crash_restarts'], 0)

    def test_108_expiry_straight_after_a_restart(self):
        """REBOOT, then B's guard expiry before any read: as case 3, A follows after its idle window
        (the former model held B's bootloader select while A followed at once)."""
        dev = self.connect()
        boots = self.sim.status()['b_boots']
        self.assertEqual(dev.call(P.REBOOT, struct.pack('<I', 2))[0], 0)      # home again, no read since
        self.wait(lambda s: s['b_boots'] > boots and s['listening'], 'home after the restart')
        dev = self.connect()
        self.expiry_test(dev)
        self.assertGreater(self.a_follows(2), 9000)
        self.assert_back_home(1)
        status = self.sim.status()
        self.assertEqual((status['a_boots'], status['b_boots'], status['a_bootloader'], status['b_bootloader']),
                         (3, 3, 0, 0))

    # -- F3: the guard on, an idle host --------------------------------------------------------------
    def test_guard_on_60_s_of_status_polls(self):
        """B's guard on, an idle host: B's keepalives keep A's idle window quiet; nothing restarts in 60 s."""
        dev = self.connect()
        self.ok(dev, P.GUARD, 1)
        self.assertEqual(dev.read_file(STATE_FILE)[0], 0)
        start = self.sim.status()['clock_ms']
        while self.sim.status()['clock_ms'] - start < 60000:
            self.assertEqual(dev.call(P.STATUS)[0], 0)
            self.sim.advance(300)
        status = self.settled()
        self.assertEqual((status['a_boots'], status['b_boots'], status['a_expiries'], status['b_expiries']), (1, 1, 0, 0))
        self.assertEqual(dev.read_file(STATE_FILE)[0], 0)
        self.ok(dev, P.GUARD, 0)

    # -- lone restarts ---------------------------------------------------------------------------------
    def test_lone_b_reset_a_follows(self):
        """B resets alone with no handler (no marker of its own): B's bootloader waits; A follows after
        its idle window (the former model held B's bootloader select); the home marker
        brings B back home."""
        dev = self.connect()
        self.ok(dev, P.HOLD, 1)                                     # home writes the marker
        self.sim.reset('b')
        self.assertGreater(self.a_follows(1), 9000)
        self.assert_back_home(1, b_boots=2)
        status = self.sim.status()
        self.assertEqual((status['a_boots'], status['b_boots']), (2, 2))

    # -- a lost link never restarts B -------------------------------------------------------------------
    def test_a_file_request_still_out_never_restarts_b(self):
        """A file request A never answered (A silent, the request lost): file commands end with link
        within their bound; meanwhile A, silent past its idle window, has restarted to wait for B. Past
        the lost limit (60 s after the request left) B still does not restart (before, F2
        restarted it there, long after A, and it hung): the menu stays on USB."""
        dev = self.connect()
        path = STATE_FILE.encode() + b'\0'
        self.assertEqual(dev.read_file(STATE_FILE)[0], 0)
        # The request is lost on its way (drop_request), then A goes silent: silencing A first would
        # leave a keepalive B sent before the request unanswered, a lost link that depends on host timing.
        self.sim.set('drop_request', 1)
        reply = {}
        thread = threading.Thread(target=lambda: reply.setdefault('r', dev.call(P.READ_FILE, path, timeout=HOST_CAP)),
                                  daemon=True)
        thread.start()
        self.wait(lambda s: s['drop_request'] == 0, 'the request out (and lost)')
        self.sim.set('a_silent', 1)                                # B, waiting on the request, sends nothing
        self.sim.advance(6000, step=None)                          # past the client's 5 s bound
        thread.join(HOST_CAP)
        self.assertEqual(reply['r'][0], ids.S_LINK)
        self.sim.set('a_silent', 0)
        self.sim.advance(30000, step=None)                         # past A's 10 s idle window, below lost_ms
        self.wait(lambda s: s['a_boots'] == 2 and s['a_bootloader'] == 1, 'A waiting for B')
        self.assertEqual(dev.call(P.STATUS)[0], 0)                 # B answers in its menu: deferred
        status = self.sim.status()
        self.assertEqual((status['b_boots'], status['b'], status['listening']), (1, 'running', 1))
        self.assertEqual(dev.call(P.STATUS)[0], 0)
        self.sim.advance(LOST_MS - 30000 + 3000, step=None)
        status = self.settled()
        self.assertEqual((status['b_boots'], status['b'], status['listening'], status['b_waits_a']), (1, 'running', 1, 0))
        self.assertEqual(dev.call(P.STATUS)[0], 0)
        self.assertEqual(dev.call(P.READ_FILE, STATE_FILE.encode() + b'\0', timeout=10.0)[0], ids.S_LINK)

    def test_a_restart_never_breaks_an_open_write_window(self):
        """B resets alone while A's write entry holds the read-write window (entered from the board):
        B's bootloader waits; A follows, closing the window (yaffs checkpoint, read-only again) before it
        restarts; nothing is left open."""
        dev = self.connect()
        data = b'written before the restart'
        self.assertEqual(dev.stage(data), 0)
        self.assertEqual(dev.file_write('/yaffs2/xy-forever/log/09b.bin', __import__('hashlib').sha256(data).digest(),
                                        10.0)[:2], (0, len(data)))
        self.ok(dev, P.HOLD, 1)                                     # home writes the marker
        value = 0x434d << 16 | 10 << 8 | 0x77                       # file_write_item, as B's ask()
        reply = self.sim.ask_a(0x11907694, 0x18, value)
        self.assertEqual(reply[0] & 0xffffffff, ~value & 0xffffffff)
        self.assertEqual(self.sim.ask_a(0x11907694, 8), (0, 0))    # op 8: the read-write window
        self.assertEqual(self.sim.status()['rw_open'], 1)
        self.sim.reset('b')
        self.a_follows(1)
        self.assert_back_home(1, b_boots=2)
        status = self.sim.status()
        self.assertEqual((status['a_boots'], status['rw_open'], status['unclean_mounts']), (2, 0, 0))
        self.assertEqual((self.dir / 'yaffs2/xy-forever/log/09b.bin').read_bytes(), data)

    # -- the model's guesses ------------------------------------------------------------------------------
    def test_recovery_holds_across_the_model_guesses(self):
        """The model guessed the bootloaders' start times (A 300 ms, B 400 ms). Across a spread the
        predictions stay: a lone restart of A leaves B at the menu on link (case 2; a power cycle is
        the way out), a guard reset of B comes back once A follows (case 3)."""
        for a_ms, b_ms in ((50, 50), (50, 1500), (1000, 100), (1000, 1500), (300, 400)):
            with self.subTest(a_boot_ms=a_ms, b_boot_ms=b_ms):
                self.sim.control('power')
                self.wait(lambda s: s['listening'] and s['a'] == 'running' and s['b'] == 'running', 'powered on')
                self.sim.set('a_boot_ms', a_ms)
                self.sim.set('b_boot_ms', b_ms)
                dev = self.connect()
                self.lone_a(dev)
                self.sim.control('power')                          # the link is lost: a power cycle
                self.wait(lambda s: s['listening'] and s['a'] == 'running' and s['b'] == 'running', 'powered on')
                dev = self.connect()
                boots = self.sim.status()['a_boots']
                self.ok(dev, P.GUARD, 1)
                self.assertEqual(dev.read_file(STATE_FILE)[0], 0)
                self.ok(dev, P.GUARD, 0)
                self.expiry_test(dev)
                self.a_follows(boots)
                dev = self.assert_back_home(1)
                self.ok(dev, P.GUARD, 0)

    def test_what_depends_on_test_108(self):
        """a_redeliver 0 (A's bootloader does not get a request A held): the former model let a lone B reset then
        waited for good on its select. Now B's bootloader sends A nothing until A has reset, so a lone B
        reset comes back the same way without it (A follows)."""
        self.sim.set('a_redeliver', 0)
        dev = self.connect()
        self.ok(dev, P.HOLD, 1)
        self.sim.reset('b')
        self.a_follows(1)
        self.assert_back_home(1, b_boots=2)
        self.assertEqual(self.sim.status()['outstanding'], 0)

    # -- the review's fixes (build 160) --------------------------------------------------------
    def test_a_stall_at_the_menu_restarts_nothing(self):
        """A's loop stalls 2 s at the menu (a rescan after the run entry, say: no probe, A's stock
        interrupt echoes the keepalives meanwhile): nothing restarts and the link still carries file
        commands."""
        dev = self.connect()
        self.assertEqual(dev.read_file(STATE_FILE)[0], 0)
        before = self.settled()
        self.sim.set('a_stall_now', 2000)
        self.sim.advance(4000)                                     # B's keepalives run on
        status = self.settled()
        self.assertEqual((status['a_boots'], status['b_boots'], status['listening']),
                         (before['a_boots'], before['b_boots'], 1))
        self.assertEqual(self.usb(dev)['crash_restarts'], 0)
        self.assertEqual(dev.read_file(STATE_FILE)[0], 0)

    def test_after_an_install_b_bootloader_is_refused_again(self):
        """The install exemption ends with the install: after an uninstall (the boot-partition writer
        ran, partition 0 selected again), a boot request (B's stock bootloader's select 1) is held
        unanswered and A follows (F1), as before the install (159: answered for the rest of the boot)."""
        import hashlib
        dev = self.connect()
        stock = sim.STOCK_OS.read_bytes()
        self.assertEqual(dev.stage(stock), 0)
        self.assertEqual(dev.uninstall(hashlib.sha256(stock).digest())[0], 0)
        boots = self.sim.status()['a_boots']
        tag = self.sim.inject(0x11907694, 0x18, 1)                 # B's bootloader: select 1
        self.wait(lambda s: s['a_boots'] > boots, 'A following', device_ms=3000)
        self.assertFalse(self.sim.injected(tag).get('done'))       # never answered by XY Forever
        self.assertEqual(self.sim.status()['partition'], 0)
        self.assertEqual(self.sim.status()['odd_selects'], 0)      # install's commands too (the review's point 3)

    def test_no_tagged_select_has_low_bits(self):
        """Every command B sends as a request (a select whose argument carries command_tag) keeps the
        argument's low three bits 0: A's bootloader's select takes them as the partition (159: B's
        sequence counter filled them). Set-default's four id-byte commands still carry raw hash bytes
        there (reported, not changed by the review's fixes): this case sends none."""
        dev = self.connect()
        for _ in range(9):                                         # past the 3-bit sequence wrap
            self.assertEqual(dev.read_file(STATE_FILE)[0], 0)      # B's read item
            self.ok(dev, P.GUARD, 1)
            self.ok(dev, P.GUARD, 0)
        data = b'0.9f'
        self.assertEqual(dev.stage(data), 0)
        self.assertEqual(dev.file_write('/yaffs2/xy-forever/log/09f.bin', __import__('hashlib').sha256(data).digest(),
                                        10.0)[:2], (0, len(data)))
        self.sim.advance(3000)                                     # keepalives and probes
        self.assertEqual(self.sim.status()['odd_selects'], 0)

    # -- B closes its link before it resets --------------------------------------------------
    def test_b_reset_refused_when_the_link_cannot_close(self):
        """B's master close does not take (SPI and DMA stay on): B does not reset. After a lone restart
        of A (no close tried: B never resets itself) a REBOOT is answered, its close fails and B stays:
        the menu with USB, file commands answer link, the count unchanged; nothing froze."""
        dev = self.connect()
        self.sim.set('b_close_stuck', 1)
        self.sim.reset('a')
        status = self.refused_after(dev)
        self.assertEqual((status['b_waits_a'], status['b_close_stuck']), (0, 1))   # no close tried yet
        self.assertEqual(self.usb(dev)['crash_restarts'], 0)
        self.assertEqual(dev.call(P.REBOOT, struct.pack('<I', 2))[0], 0)
        self.sim.advance(3000, step=None)
        status = self.settled()
        self.assertEqual((status['b_boots'], status['b'], status['listening'], status['b_waits_a'],
                          status['b_close_stuck']), (1, 'running', 1, 0, 0))      # the close was tried
        self.assertEqual(dev.call(P.STATUS)[0], 0)
        self.assertEqual(dev.call(P.READ_FILE, STATE_FILE.encode() + b'\0', timeout=10.0)[0], ids.S_LINK)

    # -- the control hooks ---------------------------------------------------------------------------
    def test_control_hooks(self):
        """expire b|a only counts out an armed guard; GUARD on arms both processors; the window is settable
        and B's wait shows in the status."""
        dev = self.connect()
        status = self.sim.status()
        self.assertEqual((status['boot_a_window_ms'], status['b_waits_a']), (400, 0))
        self.sim.set('boot_a_window_ms', 150)
        self.assertEqual(self.sim.status()['boot_a_window_ms'], 150)
        self.sim.set('boot_a_window_ms', 400)
        self.sim.expire('b')                                        # guard off: nothing happens
        self.sim.expire('a')
        self.sim.advance(300)                                       # both loops ran (the flags are consumed)
        self.assertEqual((self.sim.status()['b_expiries'], self.sim.status()['a_expiries']), (0, 0))
        self.ok(dev, P.GUARD, 1)                                    # B only: A has no guard
        for _ in range(5):
            self.sim.expire('a')
            self.sim.advance(100)
        self.assertEqual(self.sim.status()['a_expiries'], 0)
        self.ok(dev, P.GUARD, 0)


if __name__ == '__main__':
    unittest.main()
