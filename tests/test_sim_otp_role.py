"""Role isolation through the real full checker and read-only A transport."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'scripts')]
import tfw_probe
import boot_probe
import xyf_host as host
from xy_forever import paths, sim, tfw_extract, usb_proto as P
from test_tfw_check import OFFICIAL, PRIVATE

UNAVAILABLE = sim.available() or PRIVATE

@unittest.skipIf(UNAVAILABLE, UNAVAILABLE)
class RoleOtp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = sim.build()
        cls.fields = sim.otp_fields()
        cls.expected = hashlib.sha256(tfw_extract.public_key()).digest()

    def start(self, initialized=False, a_fields=True):
        temp = tempfile.TemporaryDirectory(dir=paths.SESSIONS)
        self.addCleanup(temp.cleanup)
        folder = Path(temp.name)
        root = sim.seed(folder / 'yaffs2')
        (root / 'firmware.version').write_text('1.2.1\n')
        target = root / 'xy-forever' / 'probe'
        target.mkdir()
        for path in OFFICIAL:
            (target / (path.name + '.cut')).write_bytes(tfw_extract.cut(path))
        (target / 'bad.cut').write_bytes(b'XFCT' + bytes(12))
        machine = sim.Simulator(root, folder / 'bootpart.bin', build_dir=self.build, otp=self.fields,
                                otp_a=self.fields if a_fields else None, otp_initialized=initialized,
                                a_otp_initialized=initialized, log=folder / 'sim.log').start()
        self.addCleanup(machine.stop)
        dev = host.Device(host.TcpPort(machine.address), on_log=None)
        self.addCleanup(dev.t.close)
        self.assertEqual(dev.hello()[0], 0)
        return machine, dev, folder

    def check(self, dev, role, name=None, version=1):
        name = name or OFFICIAL[0].name + '.cut'
        code, body = dev.call(P.ids.TFW_CHECK_ROLE, struct.pack('<II', version, role) +
                              ('/yaffs2/xy-forever/probe/' + name).encode() + b'\0', timeout=6)
        if code:
            return code, body
        self.assertEqual(struct.unpack_from('<II', body), (1, role))
        self.assertEqual(len(body), 48)
        return code, (struct.unpack_from('<II', body, 8), body[16:])

    def test_both_roles_full_crypto_and_isolation(self):
        for initialized in (False, True):
            with self.subTest(initialized=initialized):
                machine, dev, _ = self.start(initialized)
                for path in OFFICIAL:
                    for role in (0, 1):
                        self.assertEqual(self.check(dev, role, path.name + '.cut'), (0, ((0, 0), self.expected)))
                state = machine.status()
                self.assertEqual((state['a_otp_reads'], state['otp_reads']), (3 * len(OFFICIAL), 3 * len(OFFICIAL)))
                self.assertEqual((state['a_otp_inits'], state['otp_inits']), (0, 0) if initialized else (1, 1))
                for fail, reason in ((1, 4), (2, 5), (3, 6), (4, 7)):
                    machine.set('a_otp_fail', fail)
                    code, ((verdict, found), digest) = self.check(dev, 0)
                    self.assertEqual((code, verdict, found), (0, 1, reason))
                    if fail == 4:
                        self.assertEqual(digest, self.expected) # matching public key cannot hide wrong A AES
                    self.assertEqual(self.check(dev, 1), (0, ((0, 0), self.expected)))
                machine.stop()

    def test_missing_a_and_bad_cut_and_newer_version(self):
        machine, dev, _ = self.start(a_fields=False)
        self.assertEqual(self.check(dev, 0), (0, ((1, 4), bytes(32))))
        self.assertEqual(self.check(dev, 1), (0, ((0, 0), self.expected)))
        before = machine.status()
        for role in (0, 1):
            self.assertEqual(self.check(dev, role, 'bad.cut'), (0, ((1, 2), bytes(32))))
            self.assertEqual(self.check(dev, role, version=2), (P.ids.S_UNSUPPORTED, b''))
        machine.set('tfw_rpc_version', 2)
        self.assertEqual(self.check(dev, 0), (P.ids.S_UNSUPPORTED, b''))
        after = machine.status()
        self.assertEqual((before['a_otp_reads'], before['otp_reads']), (after['a_otp_reads'], after['otp_reads']))
        self.assertEqual((after['partition'], after['boot_writes'], after['rw_open']), (0, 0, 0))

    def test_probe_matrix_retains_readable_negative_and_cleans_cuts(self):
        machine, dev, folder = self.start()
        dev.t.close()
        machine.set('a_otp_fail', 4)
        sessions = folder / 'evidence'
        sessions.mkdir()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = tfw_probe.main(['--sim', machine.address, '--roles', 'a,b', '--keep'], sessions=sessions)
        summary = json.loads(next(sessions.glob('*/summary.json')).read_text())
        self.assertEqual(code, 0, summary['steps'])
        self.assertEqual([r['verdict'] for r in summary['roles']], ['fail', 'pass', 'fail', 'pass'])
        self.assertTrue(all(r['public_key_match'] for r in summary['roles']))
        self.assertEqual(summary['remaining_cuts'], [])
        self.assertEqual((machine.status()['a_boots'], machine.status()['b_boots']), (1, 1))
        host.SIM = None

    def test_a_rom_hang_or_fault_stops_probe_without_cleanup_or_reset(self):
        for fail in (5, 6):
            with self.subTest(fail=fail):
                machine, dev, folder = self.start()
                dev.t.close()
                machine.set('a_otp_fail', fail)
                sessions = folder / 'evidence'
                sessions.mkdir()
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    code = tfw_probe.main(['--sim', machine.address, '--roles', 'a,b', '--packages',
                                          tfw_probe.PACKAGES[0], '--check-timeout', '.3'], sessions=sessions)
                self.assertEqual(code, 1)
                summary = json.loads(next(sessions.glob('*/summary.json')).read_text())
                self.assertTrue(summary['power_cycle_needed'])
                self.assertEqual(summary['remaining_cuts'], [tfw_probe.remote(tfw_probe.PACKAGES[0])])
                self.assertEqual(next(r for r in summary['steps'] if r['step'] == 'cleanup')['result'], 'SKIP')
                self.assertFalse(any(r['step'] == 'keepalive' for r in summary['steps']))
                state = machine.status()
                self.assertEqual((state['a_boots'], state['b_boots'], state['otp_reads'], state['boot_writes']), (1, 1, 0, 0))
                machine.stop()
        host.SIM = None

    def test_package_lease_and_boot_probe_exclude_otp(self):
        machine, dev, _ = self.start()
        before = machine.status()
        token = bytes(range(32))
        self.assertEqual(dev.call(P.ids.PACKAGE_SESSION, struct.pack('<II', 1, 0) + token)[0], 0)
        self.assertEqual(self.check(dev, 0), (P.ids.S_BUSY, b''))
        self.assertEqual(dev.call(P.ids.PACKAGE_SESSION, struct.pack('<II', 1, 2) + token)[0], 0)
        # The startup sweep may close the window asynchronously; let the status loop settle it.
        for _ in range(10):
            saved, dev.hello_info = dev.hello_info, None
            try:
                code, _ = dev.call(0x64, struct.pack('<II', 0, 1))
            finally:
                dev.hello_info = saved
            if code != P.ids.S_BUSY:
                break
        self.assertEqual(code, 0)
        self.assertEqual(self.check(dev, 0), (P.ids.S_BUSY, b''))
        boot_probe.call(dev, boot_probe.END)
        after = machine.status()
        self.assertEqual((before['a_otp_reads'], before['otp_reads']), (after['a_otp_reads'], after['otp_reads']))
        self.assertEqual((after['partition'], after['boot_writes'], after['rw_open']), (0, 0, 0))
