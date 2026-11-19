"""ROM failure/init-state models through the actual B command and probe; no private key fixture."""
import contextlib
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'scripts')]
import tfw_probe
import xyf_host as host
from xy_forever import paths, sim, usb_proto as P
from test_tfw_check import package

UNAVAILABLE = sim.available() or (None if sim.STOCK_OS.is_file() else 'stock 1.2.1 fixture absent')


@unittest.skipIf(UNAVAILABLE, UNAVAILABLE)
class SimulatorOtp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = sim.build()

    def start(self, folder, initialized=False, otp=None):
        root = sim.seed(folder / 'yaffs2')
        target = root / 'xy-forever' / 'packages'
        target.mkdir()
        (target / 'synthetic.tfw').write_bytes(package())       # reaches key read before its unsigned signature fails
        machine = sim.Simulator(root, folder / 'bootpart.bin', build_dir=self.build, otp=otp,
                                otp_initialized=initialized, log=folder / 'sim.log').start()
        self.addCleanup(machine.stop)
        return machine

    def test_preinitialized_guard_and_rom_state_agree(self):
        for initialized in (False, True):
            with self.subTest(initialized=initialized), tempfile.TemporaryDirectory(dir=paths.SESSIONS) as temp:
                # A supplied zero point is invalid but observable: it distinguishes a ROM read from
                # an uninitialized ROM returning no data, without loading any private key fixture.
                machine = self.start(Path(temp), initialized, otp=bytes((9, 56)) + bytes(56))
                dev = host.Device(host.TcpPort(machine.address), on_log=None)
                try:
                    self.assertEqual(dev.hello()[0], 0)
                    code, body = dev.call(P.ids.TFW_CHECK, b'/yaffs2/xy-forever/packages/synthetic.tfw\0')
                    self.assertEqual((code, struct.unpack_from('<II', body)), (0, (1, 5)))  # public_key: zero point
                    state = machine.status()
                    self.assertEqual((state['otp_inits'], state['otp_reads']), (0 if initialized else 1, 1))
                finally:
                    dev.t.close()
                    machine.stop()

    def test_probe_reports_true_rom_hangs_and_faults_without_recovery_commands(self):
        for failure in (5, 6):
            for initialized in (False, True):
                with self.subTest(failure=failure, initialized=initialized), \
                        tempfile.TemporaryDirectory(dir=paths.SESSIONS) as temp:
                    folder = Path(temp)
                    machine = self.start(folder, initialized)
                    machine.set('otp_fail', failure)
                    firmware, sessions = folder / 'firmware', folder / 'sessions'
                    firmware.mkdir()
                    sessions.mkdir()
                    name = tfw_probe.PACKAGES[0]
                    (firmware / name).write_bytes(package())
                    out, start = io.StringIO(), time.monotonic()
                    with patch.object(tfw_probe.tfw_extract, 'public_key', return_value=bytes(56)), \
                            contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                        code = tfw_probe.main(['--sim', machine.address, '--firmware', str(firmware),
                                              '--packages', name, '--check-timeout', '.4'], sessions=sessions)
                    self.assertEqual(code, 1, out.getvalue())
                    self.assertIn('power cycle needed', out.getvalue())
                    self.assertLess(time.monotonic() - start, 4)
                    summary = json.loads(next(sessions.glob('*/summary.json')).read_text())
                    self.assertTrue(summary['power_cycle_needed'])
                    self.assertEqual(summary['remaining_cuts'], [tfw_probe.remote(name)])
                    self.assertFalse(any(row['step'] in ('hold-on', 'hold-off', 'keepalive') for row in summary['steps']))
                    state = machine.status()
                    self.assertEqual((state['a'], state['b_boots']), ('running', 1))
                    self.assertEqual(state['otp_inits'], 0 if initialized else 1)
                    self.assertEqual(state['otp_reads'], 1 if initialized else 0)
                    if failure == 6:
                        self.assertEqual(state['b'], 'crashed')
                    else:
                        # A true stuck ROM call cannot be released by clearing the fault flag.
                        machine.set('otp_fail', 0)
                        dev = host.Device(host.TcpPort(machine.address), on_log=None)
                        try:
                            with self.assertRaises((TimeoutError, OSError)):
                                dev.call(P.HELLO, timeout=.1)
                        finally:
                            dev.t.close()
                    machine.stop()
        host.SIM = None
