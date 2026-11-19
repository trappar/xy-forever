"""Probe sequencing and actual tfw.check timeout propagation, without private fixtures or a device."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
import struct
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'scripts')]
import tfw_probe as probe
import device_pass as dp
import xyf_host as host
from xy_forever import usb_proto as P

PUBLIC = bytes(range(56))                         # synthetic bytes; no key fixture is opened
HELLO = P.hello_text('0.1.0', '12345678', 'product', {'XYF_TFW_CHECK'})


class FakeKeepalive:
    def __init__(self):
        self.stops, self.starts = 0, []

    def stop_running(self):
        self.stops += 1

    def start(self, folder):
        self.starts.append(folder)
        return 12345


class FakeDevice:
    def __init__(self, archive, failure=None):
        self.archive, self.failure = archive, failure
        self.calls, self.files = [], {}

    def command(self, argv, timeout):
        self.calls.append((list(argv), timeout))
        command = argv[0]
        if command == 'status':
            return 0, 'connected fake: ' + HELLO + '\n{}\n', ''
        if command == 'put-file':
            if self.failure == 'put':
                return P.ids.S_STORAGE, 'storage', ''
            self.files[argv[2]] = Path(argv[1]).read_bytes()
            return 0, 'ok', ''
        if command == 'rm':
            assert list(self.archive.glob('cleanup-*.json')), 'cleanup must be logged before deletion'
            if self.failure == 'cleanup-timeout':
                return dp.ERROR, '', 'TimeoutError: no reply'
            self.files.pop(argv[1], None)
            return 0, 'ok', ''
        if command == 'tfw-check':
            control = argv[1] == probe.CONTROL
            if not control and self.failure in ('timeout', 'fault'):
                return (dp.ERROR if self.failure == 'timeout' else dp.NO_ANSWER), '', 'device lost'
            if not control and self.failure == 'link':
                return P.ids.S_LINK, json.dumps(dict(status='link')), ''
            verdict, reason, digest = ('fail', 'not_package', '00' * 32) if control else \
                ('pass', 'pass', hashlib.sha256(PUBLIC).hexdigest())
            if not control and self.failure == 'verdict':
                verdict, reason = 'fail', 'signature'
            return (verdict != 'pass'), json.dumps(dict(status='ok', verdict=verdict, reason=reason,
                                                        public_key_sha256=digest)), ''
        raise AssertionError('unexpected command: %r' % argv)


class ProbeSequence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=probe.paths.SESSIONS)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.firmware, self.sessions, self.archive = [self.root / p for p in ('firmware', 'sessions', 'archive')]
        self.firmware.mkdir()
        self.sessions.mkdir()
        for name in probe.PACKAGES:
            (self.firmware / name).write_bytes(b'synthetic package')

    def run_probe(self, failure=None, extra=()):
        device, keepalive, output = FakeDevice(self.archive, failure), FakeKeepalive(), io.StringIO()
        with patch.object(probe.tfw_extract, 'public_key', return_value=PUBLIC), \
                patch.object(probe.tfw_extract, 'cut', return_value=b'synthetic cut'), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            code = probe.main(['--firmware', str(self.firmware), '--check-timeout', '.25', *extra],
                              device=device, keepalive=keepalive, sessions=self.sessions, cleanup_dir=self.archive)
        return code, device, keepalive, output.getvalue()

    def test_success_removes_only_attempted_cuts_without_hold_or_guard(self):
        code, device, keepalive, out = self.run_probe()
        self.assertEqual(code, 0, out)
        commands = [a[0] for a, _ in device.calls]
        self.assertEqual(commands, ['status', 'tfw-check', 'put-file', 'tfw-check',
                                    'put-file', 'tfw-check', 'rm', 'rm'])
        self.assertEqual([a[1] for a, _ in device.calls if a[0] == 'rm'], list(map(probe.remote, probe.PACKAGES)))
        self.assertEqual(device.files, {})
        self.assertEqual((keepalive.stops, len(keepalive.starts)), (1, 1))
        summary = json.loads(next(self.sessions.glob('*/summary.json')).read_text())
        self.assertEqual(summary['remaining_cuts'], [])
        self.assertFalse(summary['power_cycle_needed'])

    def test_lost_check_stops_immediately_and_records_leftovers(self):
        for failure in ('timeout', 'fault', 'link'):
            with self.subTest(failure=failure):
                code, device, keepalive, out = self.run_probe(failure)
                self.assertEqual(code, 1)
                self.assertIn('power cycle needed', out)
                self.assertEqual([a[0] for a, _ in device.calls], ['status', 'tfw-check', 'put-file', 'tfw-check'])
                self.assertEqual(keepalive.starts, [])
                self.assertEqual(list(device.files), [probe.remote(probe.PACKAGES[0])])
                self.assertTrue(list(self.archive.glob('cleanup-*.json')))

    def test_valid_failure_reply_still_allows_cleanup_but_no_recovery(self):
        for failure in ('verdict', 'put'):
            with self.subTest(failure=failure):
                code, device, keepalive, out = self.run_probe(failure)
                self.assertEqual(code, 1)
                self.assertEqual(device.calls[-1][0], ['rm', probe.remote(probe.PACKAGES[0])])
                self.assertEqual(device.files, {})
                self.assertEqual(keepalive.starts, [])

    def test_cleanup_timeout_stops_before_the_next_cut(self):
        code, device, keepalive, out = self.run_probe('cleanup-timeout')
        self.assertEqual(code, 1)
        self.assertIn('power cycle needed', out)
        self.assertEqual(len([a for a, _ in device.calls if a[0] == 'rm']), 1)
        self.assertEqual(keepalive.starts, [])

    def test_package_names_cannot_turn_cleanup_into_another_delete(self):
        code, device, keepalive, out = self.run_probe(extra=('--packages', 'opxy_firmware_1/../../bootloader.dev'))
        self.assertEqual(code, 2)
        self.assertEqual(device.calls, [])


class CheckTimeout(unittest.TestCase):
    def run_check(self, wire):
        wire.t = SimpleNamespace(close=Mock())
        p = probe.Probe(dp.HostDevice(), SimpleNamespace(check_timeout=.125), FakeKeepalive())
        with patch.object(host, 'connect', return_value=(wire, 'fake', HELLO)):
            return p.check_file(probe.CONTROL)

    def test_timeout_reaches_the_actual_call_and_is_restored(self):
        original = host.TFW_CHECK_TIMEOUT
        wire = SimpleNamespace(call=Mock(return_value=(0, struct.pack('<II', 1, 2) + bytes(32))))
        self.assertEqual(self.run_check(wire)['reason'], 'not_package')
        wire.call.assert_called_once_with(P.ids.TFW_CHECK, probe.CONTROL.encode() + b'\0', timeout=.125)
        self.assertEqual(host.TFW_CHECK_TIMEOUT, original)

    def test_timeout_exception_reports_loss_and_restores_host_setting(self):
        original = host.TFW_CHECK_TIMEOUT
        wire = SimpleNamespace(call=Mock(side_effect=TimeoutError('no check reply')))
        with self.assertRaisesRegex(dp.StepFail, 'power cycle needed'):
            self.run_check(wire)
        self.assertEqual(host.TFW_CHECK_TIMEOUT, original)
