"""#102 crash trigger test build: never in the product; the device script's decoding; the script against the simulator."""
import contextlib
import io
from pathlib import Path
import struct
import sys
import tempfile
import unittest

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
import xyf_build as B  # noqa: E402
import xyf_host as host  # noqa: E402
from xy_forever import paths, sim  # noqa: E402
import crash_trigger as ct  # noqa: E402
import device_pass as dp  # noqa: E402

UNAVAILABLE = sim.available() or (None if sim.STOCK_OS.is_file() else 'private stock 1.2.1 os.ldr absent')


class ModeTest(unittest.TestCase):
    def test_a_test_mode_never_the_product(self):
        mode = B.CRASH_TRIGGER_MODE
        self.assertIn(mode, B.MODES)
        self.assertNotIn(mode, B.PRODUCT_MODES)
        self.assertEqual(B.product_kind(mode), 'xyf-product')             # the product plus diag.crash
        self.assertEqual(B.usb_switches(mode), B.usb_switches('xyf-product'))
        self.assertIn(mode, B.MTP_USB_MODES)                               # the product's composite too
        self.assertIn(mode, host.USB_MODES)
        self.assertEqual(B.CRASH_TRIGGER_DEFINES, ('-DXYF_CRASH_TRIGGER=1',))


@pytest.mark.sim
class ScriptTest(unittest.TestCase):
    def test_unmapped_address_is_in_no_stock_data_cplb(self):
        table = [(0x08000000, 0x3109d), (0x08010000, 0x3109d), (0x08020000, 0x3109d), (0x08030000, 0x3009d),
                 (0x04000000, 0x4109d), (0x04040000, 0x4109d), (0x80000000, 0x8109d), (0x84000000, 0x8109d),
                 (0x88000000, 0x8109d), (0x8c000000, 0x8109d), (0x70000000, 0x1009d), (0x40000000, 0x8109d),
                 (0x44000000, 0x8109d), (0x38000000, 0x1009d)]                # stock 1.2.1 at L2 0x0803c0b4
        data = b''.join(struct.pack('<II', a, d) for a, d in table) + b'\xff' * (ct.CPLB_BYTES - 8 * len(table))
        entries = ct.cplb_entries(data)
        self.assertEqual(len(entries), 14)
        self.assertIn((0x80000000, 0x84000000), entries)                   # 64 MiB pages: SDRAM to 0x90000000
        self.assertEqual(max(b for a, b in entries if a >= 0x80000000), ct.UNMAPPED)
        self.assertFalse([e for e in entries if e[0] <= ct.UNMAPPED < e[1]])

    def test_an_unexpected_error_in_a_step_is_a_fail_row(self):
        run = ct.Run(None, None, None)
        run.rows = []

        def step():
            raise KeyError('identity')
        row = run.run('trigger', 'identity', step)
        self.assertEqual((row['result'], row['detail']), ('FAIL', "KeyError: 'identity'"))

    def test_expected_screen_from_module_asm(self):
        asm = ('8ab02e10 <xyf_crash_fault>:\n8ab02e2a <xyf_crash_at_excpt>:\n'
               '8ab02e3c <xyf_crash_at_illegal>:\n8ab02e48 <xyf_crash_at_unmapped>:\n'
               '8ab03ba4 <__ZN10xy_forever6key_ui11crash_faultEm>:\n'
               '8ab03bb2:\tff e2 2f f9 \tJUMP.L 0x8ab02e10 <xyf_crash_fault>;\n'
               '8ab0f7ac <__ZN10xy_forever11usb_service13crash_commandEhPKhj>:\n'
               '8ab0f7fe:\tff e2 d3 a1 \tJUMP.L 0x8ab03ba4 <__ZN10xy_forever6key_ui11crash_faultEm>;\n'
               '8ab10888:\tff e3 92 f7 \tCALL 0x8ab0f7ac <__ZN10xy_forever11usb_service13crash_commandEhPKhj>;\n')
        self.assertEqual(ct.expected(asm, 'excpt'), (0x8ab02e2c, 0x8ab1088c))   # EXCPT: the next instruction
        self.assertEqual(ct.expected(asm, 'unmapped'), (0x8ab02e48, 0x8ab1088c))
        self.assertEqual(ct.screen_lines('unmapped', 0x8ab02e48, 0x8ab1088c),
                         ['Cause 0x26: data access to unmapped memory', 'At 0x8ab02e48, called from 0x8ab1088c',
                          'SEQSTAT 0x00000026 (or 0x00002026)'])

    @unittest.skipIf(UNAVAILABLE, UNAVAILABLE)
    def test_the_device_script_against_the_simulator(self):
        with tempfile.TemporaryDirectory(prefix='xyf-sim-crasht-', dir=str(paths.SESSIONS)) as tmp:
            tmp = Path(tmp)
            sessions = tmp / 'sessions'
            sessions.mkdir()
            root = sim.seed(tmp / 'yaffs2')

            def main(*argv):
                out = io.StringIO()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                    code = ct.main(list(argv), sessions=sessions, sleep=lambda s: None)
                return code, out.getvalue()

            def raw(address, payload):
                host.SIM = address
                link, _ = dp.HostDevice().open(5.0)
                try:
                    return link.raw(ct.T_CRASH, payload, 3.0)[0]
                finally:
                    link.close()
            product = sim.Simulator(root, tmp / 'bootpart.bin', log=tmp / 'product.txt').start()
            try:
                self.assertEqual(raw(product.address, b'\x04'), ct.ids.S_UNSUPPORTED)   # the product: no trigger
            finally:
                product.stop()
            build = sim.build(mode=B.CRASH_TRIGGER_MODE)
            simulator = sim.Simulator(root, tmp / 'bootpart.bin', build_dir=build, log=tmp / 'log.txt').start()
            try:
                for payload in (b'', b'\x00', b'\x02', b'\x05', b'\x01\x00'):       # unlisted ops (2: no misaligned)
                    self.assertEqual(raw(simulator.address, payload), ct.ids.S_UNSUPPORTED, payload)
                code, out = main('--sim', simulator.address, '--kind', 'unmapped')
                self.assertEqual(code, 3, out)
                for step in ('identity', 'cplb', 'trigger', 'silent'):
                    self.assertRegex(out, r'trigger/%s +PASS' % step)
                self.assertIn('NEED: read the OP-XY screen (kind unmapped): expect Cause 0x26', out)
                self.assertIn('B halts here', (tmp / 'log.txt').read_text())
                simulator.stop()
                simulator = sim.Simulator(root, tmp / 'bootpart.bin', build_dir=build, log=tmp / 'log.txt').start()
                code, out = main('--sim', simulator.address, '--after', '--screen', 'none: simulator')
                self.assertEqual(code, 0, out)
                self.assertEqual(list(sessions.iterdir()), [])                 # a passing pair leaves nothing
                # A read-write window open: refused (busy), and B keeps answering.
                note = tmp / 'note.txt'
                note.write_text('x\n')
                self.assertEqual(dp.HostDevice().command(['put-file', str(note), '/yaffs2/xy-forever/log/crasht.txt',
                                                          '--yes-write'], 10.0)[0], 0)
                self.assertEqual(raw(simulator.address, b'\x01'), ct.ids.S_BUSY)
                self.assertEqual(dp.HostDevice().command(['status'], 5.0)[0], 0)
            finally:
                simulator.stop()
                host.SIM = None


if __name__ == '__main__':
    unittest.main()
