"""#102: B's crash screen. Its text on the host (the device header), the screen it draws, and where the vector
is put in place and taken out again (source order; the vector itself runs only on the device)."""
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import ui_preview as P  # noqa: E402
from xy_forever import hostbuild  # noqa: E402
from xy_forever.source_text import unit_text  # noqa: E402
from xy_forever import paths, sim  # noqa: E402
import xyf_host as host  # noqa: E402

sys.path.insert(0, str(ROOT / 'scripts'))
import crash_probe  # noqa: E402

UNAVAILABLE = sim.available() or (None if sim.STOCK_OS.is_file() else 'private stock 1.2.1 os.ldr absent')

PROGRAM = r'''
#include "crash_screen.hpp"
#include <stdio.h>
int main() {
    const xyf::crash::Record records[] = {{1, 0x26, 0x8ab03d14, 0x8ab05a20}, {1, 0x2021, 0x889eed60, 0x8ab02f00},
                                          {1, 0x03, 0x8ab00010, 0}, {1, 0x3f, 0xffffffff, 0xffffffff}};
    for (const xyf::crash::Record& r : records) {
        xyf::crash::Text t;
        xyf::crash::describe(r, t);
        const xyf::ui::Chrome chrome = {"v0.1.0", false, nullptr, false, "image f458e3cc"};
        const xyf::ui::Message m = xyf::crash::message(t, &chrome);
        for (unsigned i = 0; i != m.count; ++i) printf("%s\n", m.lines[i]);
        printf("%s\n--\n", m.hint);
    }
    return 0;
}
'''


class CrashScreenTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='xyf-crash-')
        source, binary = Path(cls.tmp.name) / 'crash.cpp', Path(cls.tmp.name) / 'crash'
        source.write_text(PROGRAM)
        hostbuild.run(['/usr/bin/clang++', '-std=c++11', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
                       '-I', str(ROOT / 'target'), str(source), '-o', str(binary)], check=True, capture_output=True,
                      text=True)
        out = subprocess.run([str(binary)], check=True, capture_output=True, text=True).stdout
        cls.screens = [s.strip('\n').split('\n') for s in out.split('--\n') if s.strip()]
        cls.lib = P.build(cls.tmp.name, run=hostbuild.run)
        cls.fb = P.framebuffer(cls.lib)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_lines_name_the_cause_and_the_addresses(self):
        miss, undefined, software, unknown = self.screens
        self.assertEqual(miss[1:5], ['Cause 0x26: data access to unmapped memory',
                                     'At 0x8ab03d14, called from 0x8ab05a20', 'SEQSTAT 0x00000026', 'image f458e3cc'])
        self.assertEqual(undefined[1], 'Cause 0x21: undefined instruction')    # EXCAUSE is bits 0-5 only
        self.assertEqual(undefined[3], 'SEQSTAT 0x00002021')
        self.assertEqual(software[1], 'Cause 0x03: software exception (EXCPT)')
        self.assertEqual(unknown[1:3], ['Cause 0x3f: unknown exception', 'At 0xffffffff, called from 0xffffffff'])

    def test_every_line_fits_the_screen(self):
        # 6 px per character at scale 1; the message margin is 16 px each side.
        for screen in self.screens:
            for line in screen:
                self.assertLessEqual(len(line) * 6 - 1, P.WIDTH - 2 * 16, line)

    def test_screen_draws_within_the_visible_rows(self):
        self.lib.ui_render_crash(self.fb, 0x26, 0x8ab03d14, 0x8ab05a20)
        lit = hidden = 0
        for col in range(P.WIDTH):
            base = (P.WIDTH - 1 - col) * P.STRIDE
            for y in range(P.STRIDE):
                if self.fb[base + y]:
                    if y < P.HEIGHT: lit += 1
                    else: hidden += 1
        self.assertGreater(lit, 1000)
        self.assertEqual(hidden, 0)

    def test_vector_only_while_the_menu_has_the_screen(self):
        ui = unit_text(ROOT / 'target/key_ui.cpp')
        body = ui[ui.index('extern "C" void xy_forever_key_ui() {'):]
        body = body[:body.index('\n}\n')]
        on, off = body.index('crash_on();'), body.index('crash_off();')
        self.assertEqual((body.count('crash_on();'), body.count('crash_off();')), (1, 1))
        self.assertLess(body.index('r.stages |= stage_displayed;'), on)      # after the first frame went out
        self.assertIn('if (r.stages & stage_displayed) crash_on();', body)
        self.assertLess(on, body.index('set_key_callback(on_key);'))
        self.assertLess(off, body.index('set_key_callback(0);'))             # before the stock teardown and the hand-off
        self.assertNotIn('return', body[on:off])                             # no way out of the menu skips it
        home = unit_text(ROOT / 'target/usb_service.cpp')
        reset = home[home.index('void reset_self() {'):]
        self.assertLess(reset.index('key_ui::crash_disarm();'), reset.index('stock<0x11a03518u'))

    def test_vector_touches_only_its_record(self):
        part = (ROOT / 'target/key_ui_crash.ipp').read_text()
        self.assertIn('const uint32_t evt3 = 0x1fc0200cu;', part)           # EVT3 only: EVT5, IMASK untouched
        self.assertEqual(re.findall(r'0x1fc0\w+', part), ['0x1fc0200cu', '0x1fc02000'])
        vector = part[part.index('"xyf_crash_vector:\\n"'):part.index('".size xyf_crash_vector')]
        stores = re.findall(r'"\s*(\[[^\]]*\]) = ', vector)
        self.assertEqual(stores, ['[P0 + 4]', '[P0 + 8]', '[P0 + 12]', '[P0]'])
        self.assertEqual(vector.count('P0.L = xyf_crash_record;'), 1)
        for banned in ('CALL', 'JUMP (', 'RAISE', 'EXCPT', 'STI'):
            self.assertNotIn(banned, vector)


class CrashProbeTest(unittest.TestCase):
    """scripts/crash_probe.py: its decode, and the script against the simulator (read-only peeks)."""

    def test_decode_lists_the_handled_causes(self):
        l1 = bytearray(crash_probe.L1_BYTES)
        for cause, slot, entry in ((0x26, 0x0d, 0x11a007ec), (0x2c, 0x0d, 0x11a007ec), (0x03, 0x0e, 0x88a00000)):
            l1[crash_probe.CAUSE_SLOT - crash_probe.L1 + cause] = slot
            at = crash_probe.HANDLERS - crash_probe.L1 + slot * 8
            l1[at:at + 8] = struct.pack('<II', entry, 0x1234)
        l1[crash_probe.COUNT - crash_probe.L1] = 3
        d = crash_probe.decode(bytes(l1), struct.pack('<3I', 0x881f, 0x8010, 0x20))
        self.assertEqual(sorted(d['handled']), ['0x03', '0x26', '0x2c'])
        self.assertEqual(d['handled']['0x26'], dict(slot=0x0d, entry='0x11a007ec', argument='0x00001234'))
        self.assertEqual((d['registered'], d['software']), (3, ['0x03']))
        self.assertTrue(d['hardware_error_latched'] and d['hardware_error_masked'])

    @unittest.skipIf(UNAVAILABLE, UNAVAILABLE)
    def test_the_device_probe_script(self):
        import contextlib
        import io
        with tempfile.TemporaryDirectory(prefix='xyf-sim-crash-', dir=str(paths.SESSIONS)) as tmp:
            root = sim.seed(Path(tmp) / 'yaffs2')
            simulator = sim.Simulator(root, Path(tmp) / 'bootpart.bin', log=Path(tmp) / 'log.txt').start()
            try:
                sessions = Path(tmp) / 'sessions'
                sessions.mkdir()
                out = io.StringIO()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                    code = crash_probe.main(['--sim', simulator.address], sessions=sessions)
                self.assertEqual(code, 0, out.getvalue())
                self.assertRegex(out.getvalue(), r'probe/decode +PASS')
                self.assertEqual(list(sessions.iterdir()), [])                 # a passing simulator run leaves nothing
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                    code = crash_probe.main(['--sim', '127.0.0.1:1', '--wait', '0.5'], sessions=sessions)
                self.assertEqual(code, 3)
            finally:
                simulator.stop()
                host.SIM = None


if __name__ == '__main__':
    unittest.main()
