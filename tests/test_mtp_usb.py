"""Our MTP function driver (target/mtp_usb.*) against a host model of the stock USB stack."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from xy_forever.source_text import unit_text  # noqa: E402
from xy_forever import hostbuild  # noqa: E402


class MtpUsbTest(unittest.TestCase):
    def test_driver_against_stack_model(self):
        # Composite: descriptors (EF/02/01, IAD, CDC extras, one FIFO per endpoint number), GetDeviceInfo,
        # sessions, 1 MiB paced sink with bounded passes, source, loopback, EP0 class requests by interface (CDC
        # line coding, control lines, break; MTP), the serial ring with held urbs (flow control), serial writes
        # (one in flight, ZLP), deconfigure / bus reset / suspend mid-transfer, leave with a held urb; twice.
        with tempfile.TemporaryDirectory(prefix='xyf-mtpusb-') as tmp:
            for kick in ('0', '1'):                    # default (no kick) and the off-by-default option
                binary = Path(tmp) / ('mtpusb' + kick)
                hostbuild.run(['/usr/bin/clang++', '-std=c++11', '-Wall', '-Wextra', '-Werror',
                               '-fsanitize=address,undefined', '-fno-omit-frame-pointer', '-DXYF_MTP_USB_KICK=' + kick,
                               str(ROOT / 'target/mtp_usb.cpp'), str(ROOT / 'target/mtp_usb_test.cpp'),
                               str(ROOT / 'native/mtp_responder.cpp'),
                               '-o', str(binary)], check=True, capture_output=True, text=True)
                result = subprocess.run([str(binary)], check=True, capture_output=True, text=True)
                self.assertRegex(result.stdout, r'[1-9][0-9]* checks passed')
                if kick == '0':                          # without a kick a parked RX packet is never resumed
                    self.assertRegex(result.stdout, r'no kick: OUT stalled after 49664 bytes')
                # 186-005: a freeze in each stock step leaves the marks naming it (forked children).
                for line in ('freeze in register: last mark 2 flag-deleted',
                             "freeze in start(0x200): the controller init's 5-tick wait: last mark 4 before-start-0x200",
                             'freeze in configure (the first alloc): last mark 8 first-setup'):
                    self.assertIn(line, result.stdout)
                self.assertEqual(result.stdout.count('freeze in '), 5)
            # The model fires the USB interrupt (which mallocs) inside any unmasked heap call: it must fail.
            bad = subprocess.run([str(binary), 'unmasked-free'], capture_output=True, text=True)
            self.assertNotEqual(bad.returncode, 0)
            self.assertIn('failed', bad.stderr)

    def test_stock_binding_matches_allowlist(self):
        import re
        import xyf_build as B
        text = (ROOT / 'target/mtp_usb_stock.cpp').read_text()
        called = set(re.findall(r'stock<0x([0-9a-f]{8})u', text))
        self.assertEqual(called, B.MTP_USB_CALLS | B.MTP_USB_KICK_CALLS | {'88a35044', '88a34fec'})
        # The kick calls exist only under XYF_MTP_USB_KICK, which defaults to 0.
        kick = text[text.index('#if XYF_MTP_USB_KICK'):text.index('#endif', text.index('#if XYF_MTP_USB_KICK'))]
        self.assertEqual(set(re.findall(r'stock<0x([0-9a-f]{8})u', kick.split('#else')[0])), B.MTP_USB_KICK_CALLS)
        self.assertIn('#define XYF_MTP_USB_KICK 0', (ROOT / 'target/mtp_usb.hpp').read_text())
        self.assertTrue(B.FORBIDDEN_TARGETS.isdisjoint(B.MTP_USB_CALLS))
        self.assertFalse(any(a.startswith('889f') for a in B.MTP_USB_CALLS))   # no storage client

    def test_rx_service_guard_before_stock_call(self):
        # 88a0ba08 takes the EP0 path for 0: the wrapper refuses anything but bulk OUT before calling it.
        import re
        text = (ROOT / 'target/mtp_usb_stock.cpp').read_text()
        body = re.search(r'void kick_rx\(U32 n\) \{(.*?)\n\}', text, re.S).group(1)
        self.assertLess(body.index('if (!rx_service_allowed(n)) return;'), body.index('stock<0x88a0ba08u'))
        self.assertIn('return ep_number == (kBulkOut & 0x0fu) || ep_number == (kSerialOut & 0x0fu);',
                      (ROOT / 'target/mtp_usb.hpp').read_text())


class MtpUsbBuildGateTest(unittest.TestCase):
    def setUp(self):
        import xyf_build as B
        self.B = B

    def test_no_calls_no_gate(self):
        self.assertEqual(self.B.check_mtp_usb(None, 'xyf-product', {'88a35044'}), set())

    def test_refused_in_product_and_unnamed_modes(self):
        for mode in ('xyf-product', 'xyf-stage2', 'xyf-stage2-menu', 'xyf-mtp', None):
            with self.assertRaises(SystemExit if self._exits() else Exception):
                self.B.check_mtp_usb(None, mode, {'88a092ee'})

    def test_kick_calls_refused_in_every_mode_but_the_test_build(self):
        # The kicks: MTP only, first in a test build (xyf-mtp-usb).
        for mode in [m for m in self.B.MODES if m != self.B.MTP_USB_MODE] + ['xyf-mtp']:
            for call in sorted(self.B.MTP_USB_KICK_CALLS):
                with self.assertRaises(SystemExit if self._exits() else Exception, msg=(mode, call)):
                    self.B.check_mtp_usb(None, mode, {call})
        self.assertIn(self.B.MTP_USB_MODE, self.B.MODES)
        self.assertNotIn(self.B.MTP_USB_MODE, self.B.PRODUCT_MODES)
        self.assertEqual(self.B.product_kind(self.B.MTP_USB_MODE), 'xyf-product')
        self.assertEqual(self.B.usb_switches(self.B.MTP_USB_MODE), self.B.usb_switches('xyf-product'))   # device_pass

    def test_test_build_admits_kicks_with_its_spans(self):
        from xy_forever.paths import STOCK_121
        boot = STOCK_121 / 'bootloader.ldr'
        if not boot.exists():
            self.skipTest('private bootloader absent')
        allowed = self.B.check_mtp_usb(boot.read_bytes(), self.B.MTP_USB_MODE, {'88a092ee', '88a0ba08', '88a094aa'})
        self.assertEqual(allowed, self.B.MTP_USB_CALLS | self.B.MTP_USB_KICK_CALLS)
        self.assertTrue(self.B.FORBIDDEN_TARGETS.isdisjoint(allowed))

    def test_ids_are_the_stock_pair(self):                  # the composite reuses the stock pair
        self.assertEqual(self.B.mtp_usb_ids(), self.B.MTP_USB_IDS)
        self.assertEqual(self.B.MTP_USB_IDS, (0x2367, 0x0021))
        self.assertNotEqual(self.B.mtp_usb_pid(), 0)

    def test_probe_script_matches_the_device(self):
        import importlib.util
        import re
        spec = importlib.util.spec_from_file_location('mtp_usb_probe', ROOT / 'scripts/mtp_usb_probe.py')
        script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(script)                    # imports only: pyusb is loaded in main()
        service = unit_text(ROOT / 'target/usb_service.cpp')
        self.assertIn('const uint8_t t_mtp_usb = 0x%02x;' % script.T_MTP_USB, service)
        self.assertIn('const U32 probe_words = %d;' % len(script.PROBE_WORDS), service)
        names = re.search(r'enum : U32 \{ (p_state.*?) \};', service, re.S).group(1)
        self.assertEqual([n.strip()[2:] for n in names.split(',')],
                         [w[:-3] if w.endswith('_ms') else w for w in script.PROBE_WORDS])
        driver = re.search(r'const U32 driver\[\] = \{(.*?)\};', service, re.S).group(1)
        fields = [re.sub(r'U32\((.*)\)', r'\1', f.strip()) for f in driver.split(',')]
        self.assertEqual([f.replace('st.', '').replace('mu::state()', 'state') for f in fields], list(script.DRIVER_WORDS))
        self.assertEqual((script.VID, script.PID), self.B.MTP_USB_IDS)
        self.assertIn('op_test_sink = 0x%04x' % script.OP_SINK, (ROOT / 'target/mtp_usb.hpp').read_text())
        self.assertIn("'mtp-usb'", repr(self.B.hello_defines(self.B.MTP_USB_MODE)[-1].split('"')[1:2]))

    def _script(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('mtp_usb_probe', ROOT / 'scripts/mtp_usb_probe.py')
        script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(script)
        return script

    def test_composite_started_once(self):
        # Composite: register, start(0x200), never a stop or a start inside the boot but the leave
        # (stop(0): GP timer 7 kept) before a restart or a boot; the flag deleted before anything starts.
        import re
        stock = (ROOT / 'target/mtp_usb_stock.cpp').read_text()
        self.assertIn('void stop(U32) { stock<0x88a34fecu, void>(U32(kStopKeepTimer)); }', stock)
        self.assertIn('void start(U32 flags) { stock<0x88a35044u, void>(flags); }', stock)
        header = (ROOT / 'target/mtp_usb.hpp').read_text()
        self.assertIn('kCompositeFlags = 0x200,', header)
        driver = (ROOT / 'target/mtp_usb.cpp').read_text()
        begin = re.search(r'bool begin\(const Port& port\) \{(.*?)\n\}', driver, re.S).group(1)
        self.assertNotIn('stop(', begin)
        self.assertEqual(re.findall(r'port\.start\((\w+)\)', driver), ['kCompositeFlags'])
        self.assertEqual(len(re.findall(r'port\.stop\(kStopKeepTimer\)', driver)), 1)
        self.assertLess(begin.index('register_driver'), begin.index('port.start(kCompositeFlags)'))
        service = unit_text(ROOT / 'target/usb_service.cpp')
        self.assertNotIn('start_serial', service)
        boot = re.search(r'void usb_begin\(\) \{(.*?)\n\}', service, re.S).group(1)
        order = ['C::stat(flag_path', 'C::unlink(flag_path)', 'mu::mark(mu::mark_flag_found)', 'probe_spans()',
                 'mu::begin(mu::stock_port())', 'composite_on = 1', 'usb_start()']
        self.assertEqual([boot.index(x) for x in order], sorted(boot.index(x) for x in order))
        self.assertIn('const char flag_path[] = "/yaffs2/xy-forever/tmp/composite.flag";', service)
        end = re.search(r'void fallback_restart\(\) \{(.*?)\n\}', service, re.S).group(1)
        order = ['mu::mark(mu::mark_fallback_restart)', 'mu::leave()', 'composite_on = 0', 'result_text(marks_text)',
                 'file_put(result_path', 'marks_end()', 'settle_writes(false)', 'home_reboot(reboot_menu)', 'menu_usb = 0']
        self.assertEqual([end.index(x) for x in order], sorted(end.index(x) for x in order))
        self.assertNotIn('usb_start()', end)
        self.assertIn('const U32 composite_hello_ms = 20000, mtp_seen_ms = 5000;', service)
        turn = re.search(r'void composite_turn\(\) \{(.*?)\n\}', service, re.S).group(1)
        self.assertIn('if (!probe[p_hello] && now() - probe[p_started] >= composite_hello_ms) fallback_restart();', turn)
        # The product's serial path is the stock one: every composite branch sits under XYF_MTP_USB.
        start = service.index('void menu_start_usb() {')
        self.assertIn('#if defined(XYF_MTP_USB)\n    usb_deferred = 1;', service[start:start + 800])
        self.assertIn('const U32 stack_attached = 0x88a137a0u;', service)

    def test_device_info_parser_reads_the_device_dataset(self):
        # Run 005 (186-006): the parser read the header-stripped body at offset 12. The body as device_info()
        # builds it (target/mtp_usb.cpp) is 133 bytes, what the host received.
        import struct
        script = self._script()

        def string(text):
            return bytes([len(text) + 1]) + (text + '\0').encode('utf-16-le')

        def array(values):
            return struct.pack('<I%dH' % len(values), len(values), *values)
        body = struct.pack('<HIH', 100, 6, 100) + string('') + struct.pack('<H', 0) + \
            array([0x1001, 0x1002, 0x1003, 0x9801, 0x9802, 0x9803]) + array([]) * 4 + \
            string('XY Forever') + string('OP-XY (XY Forever)') + string('3.2') + string('XYF0001')
        self.assertEqual(len(body), 133)
        info = script.parse_device_info(body)
        self.assertEqual((info['standard'], info['vendor_extension'], info['model'], info['serial']),
                         (100, 6, 'OP-XY (XY Forever)', 'XYF0001'))
        self.assertIn('0x9801', info['operations'])
        lines = []
        script.log_container(lines.append, struct.pack('<IHHI', 12, 3, 0x2001, 1))
        self.assertIn('0c00000003000120' + '01000000', lines[0])

    def test_config_record_is_the_stock_size(self):
        # the stock config record is 0x18 (n_iads at +0x10, IAD array at +0x14); ours ends in two zeros.
        header = (ROOT / 'target/mtp_usb.hpp').read_text()
        self.assertIn('uint32_t n_iads; const struct Iad* iads; };', header)
        self.assertIn('sizeof(ConfigDesc) == 0x18', header)
        self.assertIn('const ConfigDesc kConfig = {kName, 0xc0, 50, 0, 3, kIfaces, 1, kIads};',
                      (ROOT / 'target/mtp_usb.cpp').read_text())
        self.assertIn('static_assert(sizeof(Iad) == 0xc && __builtin_offsetof(Iad, first) == 7 && sizeof(CdcExtra) == 0x3c',
                      header)

    def test_marks_match_the_device(self):
        import re
        script = self._script()
        driver = (ROOT / 'target/mtp_usb.cpp').read_text()
        names = re.search(r'kMarkNames\[mark_count\] = \{(.*?)\};', driver, re.S).group(1)
        names = re.findall(r'"([^"]*)"', names)
        self.assertEqual(names[1:], [n for n, _ in script.MARKS])
        self.assertEqual(len(script.MARKS), 14)
        self.assertEqual(script.PASS_MARKS, list(range(1, 7)) + [8, 9, 10, 11])
        service = unit_text(ROOT / 'target/usb_service.cpp')
        self.assertIn('const char marks_path[] = "%s";' % script.MARKS_PATH, service)
        self.assertTrue(script.MARKS_PATH.startswith('/yaffs2/xy-forever/tmp/'))
        # The whole file fits one 1 KiB write: header + 15 lines of "NN <10 digits> <name>".
        header = len('xy-forever mtp-usb composite marks, build 186: step ms name\n')
        self.assertLessEqual(header + sum(3 + 10 + 1 + len(n) + 1 for n in names[1:]), 1024)
        text = 'xy-forever mtp-usb probe marks, build 186: step ms name\n01 1200 checks-passed\n05 1290 before-start-0\n'
        head, marks = script.parse_marks(text)
        self.assertTrue(head.startswith('xy-forever mtp-usb'))
        self.assertEqual(marks, [dict(step=1, ms=1200, name='checks-passed'), dict(step=5, ms=1290, name='before-start-0')])
        # 186-006: the result file is op 1's reply as text; the script turns it back into the reply body.
        self.assertIn('const char result_path[] = "%s";' % script.RESULT_PATH, service)
        words = list(range(1, len(script.PROBE_WORDS) + len(script.DRIVER_WORDS) + 1))
        text = ('xy-forever mtp-usb probe result, build 186\nprobe %s\ndriver %s\nsha256 %s\nrefusal %s\n' % (
            ' '.join('%08x' % w for w in words[:len(script.PROBE_WORDS)]),
            ' '.join('%08x' % w for w in words[len(script.PROBE_WORDS):]), 'ab' * 32,
            ' '.join(['00000000'] * len(script.REFUSAL_WORDS))))
        probe, driver, digest = script.decode_result(script.result_body(text))
        self.assertEqual((probe['state'], driver['handler_urbs_in'], digest), ('deferred', words[-1], 'ab' * 32))
        self.assertEqual(script.decode_refusal(script.result_body(text))['check'], 'none')
        for label in ('"probe", probe, probe_words', '"driver", driver, n_driver_words',
                      '"refusal", refusal, gate::record_words'):
            self.assertIn(label, service)
        source = (ROOT / 'scripts/mtp_usb_probe.py').read_text()
        self.assertIn("'--no-trial'", source)
        self.assertIn("'--marks-only'", source)
        self.assertNotIn('retry', source.lower().replace('never retries', ''))

    def test_refusal_words_match_the_device(self):
        # 186-002: op 1 ends with why the last start answered identity (probe_why, the start's check order).
        import importlib.util
        import re
        import struct
        spec = importlib.util.spec_from_file_location('mtp_usb_probe', ROOT / 'scripts/mtp_usb_probe.py')
        script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(script)
        service = unit_text(ROOT / 'target/usb_service.cpp')
        gate = (ROOT / 'target/mtp_usb_gate.hpp').read_text()
        self.assertIn('const uint32_t record_words = %d;' % len(script.REFUSAL_WORDS), gate)
        names = re.search(r'enum : uint32_t \{ (r_check.*?) \};', gate, re.S).group(1)
        self.assertEqual([n.strip()[2:] for n in names.split(',')],
                         ['check', 'index', 'address', 'length', 'crc', 'failing'])
        self.assertIn('enum Check : uint32_t { check_none, check_usb, check_engine, check_probe };', gate)
        self.assertEqual(len(script.REFUSAL_CHECKS), 4)
        self.assertIn('uint32_t probe_refusal[gate::record_words]', service)
        head = 4 * (len(script.PROBE_WORDS) + len(script.DRIVER_WORDS)) + 32
        self.assertIsNone(script.decode_refusal(bytes(head)))           # a 186-001 reply
        body = bytes(head) + struct.pack('<6I', 1, 65, 0x88a13460, 0x344, 0x12345678, 2)
        self.assertEqual(script.decode_refusal(body),
                         dict(check='boot-time identity() usb_spans.hpp failed', index=65, address='0x88a13460',
                              length='0x00000344', crc_now='0x12345678', failing=2))

    def test_start_gate_against_span_model(self):
        # 186-003: the start uses the boot-time identity() result and still
        # scans engine_identity()'s and probe_spans()'s spans: census words changed -> start; an MTP or the
        # probe span changed -> refused, naming the check and span; boot-time check failed -> refused.
        with tempfile.TemporaryDirectory(prefix='xyf-mtpgate-') as tmp:
            binary = Path(tmp) / 'gate'
            hostbuild.run(['/usr/bin/clang++', '-std=c++11', '-Wall', '-Wextra', '-Werror',
                           '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                           str(ROOT / 'target/mtp_usb_gate_test.cpp'), str(ROOT / 'target/xyf_crc32.cpp'),
                           '-o', str(binary)], check=True, capture_output=True, text=True)
            result = subprocess.run([str(binary)], check=True, capture_output=True, text=True)
            self.assertRegex(result.stdout, r'[1-9][0-9]* checks passed')

    def test_start_does_not_repeat_identity(self):
        import re
        service = unit_text(ROOT / 'target/usb_service.cpp')
        start = re.search(r'void usb_begin\(\) \{(.*?)\n\}', service, re.S).group(1) + \
            re.search(r'U32 trial_start\(\) \{(.*?)\n\}', service, re.S).group(1)
        spans = re.search(r'gate::Check probe_spans\(\) \{(.*?)\n\}', service, re.S).group(1)
        code = '\n'.join(line.split('//')[0] for line in (start + spans).splitlines())
        self.assertNotIn('identity(', code)                   # no check is re-run by name: the gate scans
        self.assertIn('gate::refusal<crc_at>(menu_usb == 2u && !usb_off, engine, spans, probe_refusal)', spans)
        self.assertIn('{xyf::mtp_spans::table, xyf::mtp_spans::count}', spans)
        self.assertIn('{xyf::mtp_usb_spans::table, xyf::mtp_usb_spans::count}', spans)
        self.assertLess(start.index('probe[p_gate] = probe_spans();'), start.index('mu::begin(mu::stock_port())'))
        self.assertIn('if (probe_spans()) return P::s_identity;', start)
        # Only the test build compiles the gate; menu_start_usb (product) still runs identity() at menu start.
        self.assertIn('usb_off = identity() ? 1u : file_client::identity() ? 2u : 0u;', service)
        include = service.index('#include "mtp_usb_gate.hpp"')
        self.assertLess(service.rindex('#if defined(XYF_MTP_USB)', 0, include), include)
        self.assertGreater(service.index('#endif', include), include)

    def test_tx_start_span_in_its_own_set(self):
        # The product's run-time engine check reads mtp_spans.hpp: the TX start span lives only in the
        # MTP USB mode's set, and no product source includes that header.
        self.assertIn(self.B.MTP_USB_TX_START_SPAN, [(a, n) for a, n, _ in self.B.mtp_usb_spans()])
        self.assertNotIn(self.B.MTP_USB_TX_START_SPAN, [(a, n) for a, n, _ in self.B.mtp_spans()])
        spans = self.B.mtp_spans() + self.B.mtp_usb_spans()
        self.assertTrue(all(any(a <= int(c, 16) < a + n for a, n, _ in spans) for c in self.B.MTP_USB_CALLS))
        for path in (ROOT / 'target').glob('*.?pp'):                 # host tests (*_test.cpp) are no build source
            if '#include "mtp_usb_spans.hpp"' in path.read_text() and not path.name.endswith('_test.cpp'):
                self.assertIn('defined(XYF_MTP_USB)', path.read_text(), path.name)

    def test_mtp_spans_match_bootloader(self):
        from xy_forever.paths import STOCK_121
        boot = STOCK_121 / 'bootloader.ldr'
        if not boot.exists():
            self.skipTest('private bootloader absent')
        spans = self.B.mtp_spans() + self.B.mtp_usb_spans()
        self.B.check_spans(boot.read_bytes(), spans, 'MTP USB', self.B.MTP_USB_CALLS, spans)

    def _exits(self):
        try:
            self.B.require(False, 'probe')
        except SystemExit:
            return True
        except Exception:
            return False
        return False


if __name__ == '__main__':
    unittest.main()
