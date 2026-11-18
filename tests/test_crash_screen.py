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
    // B's where word (#114): in W1; none now, the last W12; never; in W3 after W12 (the last half stays).
    const uint32_t wheres[] = {0x00010001, 0x000c0000, 0, 0x000c0003};
    for (unsigned i = 0; i != 4; ++i) {
        const xyf::crash::Record& r = records[i];
        xyf::crash::Text t;
        xyf::crash::describe(r, wheres[i], t);
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
        self.assertEqual(miss[1:6], ['Cause 0x26: data access to unmapped memory',
                                     'At 0x8ab03d14, called from 0x8ab05a20', 'SEQSTAT 0x00000026', 'Waiting in W1',
                                     'image f458e3cc'])
        self.assertEqual(undefined[1], 'Cause 0x21: undefined instruction')    # EXCAUSE is bits 0-5 only
        self.assertEqual(undefined[3], 'SEQSTAT 0x00002021')
        self.assertEqual(software[1], 'Cause 0x03: software exception (EXCPT)')
        self.assertEqual(unknown[1:3], ['Cause 0x3f: unknown exception', 'At 0xffffffff, called from 0xffffffff'])

    def test_where_line_names_the_wait(self):
        # #114: the wait B was in at the fault, else the last one it left, else none.
        self.assertEqual([s[4] for s in self.screens],
                         ['Waiting in W1', 'Not waiting; last wait W12', 'No wait yet', 'Waiting in W3'])

    def test_every_line_fits_the_screen(self):
        # 6 px per character at scale 1; the message margin is 16 px each side.
        for screen in self.screens:
            for line in screen:
                self.assertLessEqual(len(line) * 6 - 1, P.WIDTH - 2 * 16, line)

    def test_screen_draws_within_the_visible_rows(self):
        self.lib.ui_render_crash(self.fb, 0x26, 0x8ab03d14, 0x8ab05a20, 0x00030003)
        lit = hidden = 0
        for col in range(P.WIDTH):
            base = (P.WIDTH - 1 - col) * P.STRIDE
            for y in range(P.STRIDE):
                if self.fb[base + y]:
                    if y < P.HEIGHT: lit += 1
                    else: hidden += 1
        self.assertGreater(lit, 1000)
        self.assertEqual(hidden, 0)

    def test_where_corner_is_small_and_top_right(self):
        # #114: the code a wait past 500 ms paints over the menu: accent pixels only in the top-right corner box.
        self.lib.ui_render_menu(self.fb, 0, b'')
        plain = list(self.fb)
        self.lib.ui_render_where(self.fb, 12)
        changed = [(P.WIDTH - 1 - i // P.STRIDE, i % P.STRIDE) for i, (a, b) in enumerate(zip(plain, self.fb)) if a != b]
        self.assertTrue(changed)
        self.assertGreaterEqual(min(x for x, _ in changed), P.WIDTH - 24)    # "W12": 17 px of text, 2 px each side
        self.assertLess(max(y for _, y in changed), 11)

    def test_where_paints_only_from_a_wait_with_the_screen(self):
        part = (ROOT / 'target/where.hpp').read_text()
        self.assertIn('paint_ms = 500;', part)
        ui = (ROOT / 'target/key_ui_where.ipp').read_text()
        wait = ui[ui.index('void where_wait() {'):ui.index('void where_leave(')]
        self.assertIn('if (!where_screen || where_shown) return;', wait)       # once, and only while the menu has it
        self.assertIn('(word(0x1fc02108u) & 0x7fe0u)', wait)                    # never from an interrupt (IPEND)
        crash = (ROOT / 'target/key_ui_crash.ipp').read_text()
        on, off = crash[crash.index('void crash_on() {'):], crash[crash.index('void crash_off() {'):]
        self.assertLess(on.index('where_screen = 1;'), on.index('\n}\n'))       # crash_on: the menu has the screen
        self.assertLess(off.index('where_screen = 0;'), off.index('\n}\n'))     # crash_off: before the hand-off
        display = (ROOT / 'target/key_ui_display.ipp').read_text()
        self.assertLess(display.index('draw_screen(reinterpret_cast'), display.index('where_draw(reinterpret_cast'))

    def test_every_blocking_wait_on_b_has_a_code(self):
        # #114: each code is set at exactly the waits it names; the USB log write never paints.
        def count(name, code):
            return unit_text(ROOT / 'target' / name).count('key_ui::Where where(xyf::where::%s);' % code)
        self.assertEqual(count('file_client.cpp', 'file_answer'), 1)
        for code, n in (('link_settle', 1), ('command', 1), ('check_blob', 1), ('catalog_blob', 3), ('stage2_blob', 1),
                        ('handshake', 1), ('catalog_first', 1), ('link_close', 1), ('boot_image', 2)):
            self.assertEqual(count('file_boot.cpp', code), n, code)
        self.assertEqual((count('usb_service.cpp', 'usb_reply'), count('usb_service.cpp', 'usb_log')), (1, 1))
        log = unit_text(ROOT / 'target/usb_service.cpp')
        log = log[log.index('bool raw_send('):log.index('void log_line(')]
        self.assertNotIn('where.wait()', log)

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
