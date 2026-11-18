"""#102 crash trigger test build: never in the product; the device script's decoding; the script against the simulator."""
import contextlib
import io
from pathlib import Path
import re
import struct
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
import xyf_build as B  # noqa: E402
import xyf_host as host  # noqa: E402
from xy_forever import paths, sim  # noqa: E402
import crash_trigger as ct  # noqa: E402
import device_pass as dp  # noqa: E402

UNAVAILABLE = sim.available() or (None if sim.STOCK_OS.is_file() else 'private stock 1.2.1 os.ldr absent')
NAMES = ('crash_command', 'crash_fault', 'crash_armed_now', 't_crash', 'XYF_CRASH_TRIGGER')


def guarded(text):
    """Lines naming the trigger outside an #if that names XYF_CRASH_TRIGGER (a tiny preprocessor walk)."""
    stack, outside = [], []
    for n, line in enumerate(text.splitlines(), 1):
        directive = line.strip()
        if directive.startswith('#if'):
            stack.append('XYF_CRASH_TRIGGER' in directive)
        elif directive.startswith('#endif'):
            stack.pop()
        elif any(name in line for name in NAMES) and not any(stack) and not directive.startswith('//'):
            outside.append('%d: %s' % (n, line.strip()))
    return outside


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

    def test_only_the_test_mode_compiles_or_links_it(self):
        src = (ROOT / 'xyf_build.py').read_text()
        self.assertEqual(src.count('CRASH_TRIGGER_DEFINES if'), 1)
        self.assertIn("(CRASH_TRIGGER_DEFINES if mode_name == CRASH_TRIGGER_MODE and name in ('usb_service', 'key_ui')", src)
        # The verifier: each trigger symbol linked exactly in the test mode (the product's check build fails otherwise).
        self.assertIn("require(has('crash_command') == trigger and has('xyf_crash_fault') == trigger and "
                      "has('crash_fault') == trigger,", src)
        self.assertIn('trigger = mode_name == CRASH_TRIGGER_MODE\n', src)
        self.assertNotIn('XYF_CRASH_TRIGGER', (ROOT / 'protocol/v1.json').read_text())   # in no build's cmds

    def test_target_code_only_under_its_switch(self):
        for name in ('key_ui.cpp', 'key_ui.hpp', 'key_ui_crash.ipp', 'usb_service.cpp', 'usb_service_home.ipp',
                     'usb_service_crash_trigger.ipp'):
            self.assertEqual(guarded((ROOT / 'target' / name).read_text()), [], name)
        part = (ROOT / 'target/key_ui_crash.ipp').read_text()
        fault = part[part.index('"xyf_crash_fault:\\n"'):part.index('".size xyf_crash_fault')]
        self.assertNotRegex(fault, r'\] = ')                               # reads only: no store
        self.assertEqual(re.findall(r'(EXCPT \d|\.short 0x\w+|P0\.H = 0x\w+)', fault),
                         ['EXCPT 1', '.short 0x0001', 'P0.H = 0x9000'])
        self.assertEqual(re.findall(r'R1 = (\d);', fault), ['1', '3', '4'])          # no kind 2 (misaligned)
        self.assertIn('kind == 2', (ROOT / 'target/usb_service_crash_trigger.ipp').read_text())
        usb = (ROOT / 'target/usb_service_crash_trigger.ipp').read_text()
        for refusal in ('writes_due', 'closing', 'mounted_stale', 'job_running()', 'put_expected', 'crash_armed_now()'):
            self.assertIn(refusal, usb)


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

    def test_cause_names_are_the_screens(self):
        screen = (ROOT / 'target/crash_screen.hpp').read_text()
        for kind, (_, cause, name, _) in ct.KINDS.items():
            if cause >= 0x10:
                self.assertIn('{0x%02x, "%s"}' % (cause, name), screen, kind)
            else:
                self.assertIn('"%s"' % name, screen, kind)

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
