"""XY Forever product build (--xyf-product): boot model v3 (Test 065), one link session per
boot; file/library boot with the XY Forever menu as home for every USB command, in the wide
cold layout. No device access."""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from xy_forever.source_text import unit_text  # noqa: E402
import xyf_build as builder
from xy_forever.paths import DECRYPTED, SESSIONS


class ProductLayoutTest(unittest.TestCase):
    def test_only_the_product_modes_are_wide(self):
        for mode in builder.MODES:
            wide = builder.product_kind(mode) in builder.PRODUCT_MODES     # the MTP USB test build is the product's
            self.assertEqual(builder.layout(mode), (0x8ab00000, 0x8ab80000 if wide else 0x8ab0c000,
                                                    0x8ab84000 if wide else 0x8ab10000), mode)

    def test_one_product_mode(self):
        # xyf-product-v2 (build 064's two-session flow) was removed.
        self.assertEqual(builder.PRODUCT_MODES, ('xyf-product',))
        self.assertNotIn('xyf-product-v2', builder.MODES)
        src = (ROOT / 'xyf_build.py').read_text()
        for name in ('WIDE', 'FILE_WRITE', 'PLAIN_IMAGE', 'SERVICE_SCREEN', 'USB_MENU', 'MENU_ON_FAILURE',
                     'SERVICE_ON_MARKER', 'USB_SET_DEFAULT'):
            self.assertIs(getattr(builder, name + '_MODES'), builder.PRODUCT_MODES, name)
        self.assertEqual(builder.ONE_SESSION_MODES, ('xyf-product',))
        self.assertEqual(len(re.findall(r'\bmode in ONE_SESSION_MODES\b', src)), 1)

    def test_wide_layout_is_a_512k_window_then_the_stack_below_the_stage2_window(self):
        base, stack, end = builder.layout('xyf-product')
        self.assertEqual(stack - base, 512 * 1024)                  # the code window
        self.assertEqual(end - stack, 0x4000)                       # same 16 KiB stack, above the code
        self.assertEqual(builder.STAGE2_WINDOW, (0x8ab90000, 0x8ac00000))
        self.assertLessEqual(end, builder.STAGE2_WINDOW[0])         # below the (dormant) window, no overlap
        self.assertIn((builder.MODULE, builder.WIDE_STACK_END), builder.PROBE_PROTECTED)

    def test_memory_map_audit(self):
        builder.check_memory_map()                                   # the real layout passes
        self.assertEqual(builder.FREE_GAP, (0x8aa65bf4, 0x8b000000))
        bad = {'WIDE_STACK': 0x8afff000, 'WIDE_STACK_END': 0x8b003000}            # into A's job region
        for patch in (bad, {'STAGE2_WINDOW': (0x8ab80000, 0x8ac00000)},           # over the stack
                      {'STAGE2_WINDOW': (0x8afe0000, 0x8b010000)},                # into the job region
                      {'MODULE': 0x8aa00000},                                     # into the stock arena
                      {'WIDE_STACK_END': 0x8ab88000},                             # not a 16 KiB stack
                      {'FILE_STAGING': (0x8b800000, 0x8cc00000)}):                # boot copy reaches the module
            saved = {k: getattr(builder, k) for k in patch}
            try:
                for k, v in patch.items():
                    setattr(builder, k, v)
                with self.assertRaises(Exception, msg=repr(patch)):
                    builder.check_memory_map()
            finally:
                for k, v in saved.items():
                    setattr(builder, k, v)
        self.assertIn('    check_memory_map()\n    mapped = Firmware(boot)', (ROOT / 'xyf_build.py').read_text())

    def test_every_copy_of_the_wide_layout_agrees(self):
        stack_end = '%#xu' % builder.WIDE_STACK_END
        for name in ('xy_forever/tfw_extract.py', 'xy_forever/modpatch.py'):
            self.assertIn("PRODUCT_STACK_END = '%s'" % stack_end, (ROOT / name).read_text(), name)
        self.assertIn('::xyf_sim::host(%#xu)' % builder.WIDE_STACK, (ROOT / 'xy_forever/sim.py').read_text())
        self.assertIn('return %#xu; }' % (builder.WIDE_STACK + 0x200), (ROOT / 'sim/runtime.cpp').read_text())
        layout = (ROOT / 'target/cold_layout.hpp').read_text()
        self.assertIn('#define XYF_STACK_PAINT_FLOOR (XYF_STACK_END - 0x8000u)', layout)
        for name in ('key_ui.cpp', 'file_boot.cpp'):
            self.assertIn('if (begin < XYF_STACK_PAINT_FLOOR) begin = XYF_STACK_PAINT_FLOOR;',
                          unit_text(ROOT / 'target' / name), name)

    def test_code_uses_the_layout_define(self):
        header = (ROOT / 'target/cold_layout.hpp').read_text()
        self.assertIn('#define XYF_STACK_END 0x8ab10000u', header)
        for name in ('cold.cpp', 'key_ui.cpp', 'file_boot.cpp', 'peer_services.cpp'):
            text = unit_text(ROOT / 'target' / name)
            self.assertIn('#include "cold_layout.hpp"', text, name)
            self.assertNotIn('0x8ab10000u', text, name)

    def test_entry_stack_comes_from_the_layout(self):
        src = (ROOT / 'xyf_build.py').read_text()
        self.assertIn("entry_source.replace('    P0.H = 0x8ab1;\\n    P0.L = 0;\\n', '    P0.H = %#x;\\n    P0.L = %d;\\n'", src)
        self.assertIn('% (stack_end >> 16, stack_end & 0xffff)))', src)
        self.assertIn('    P0.H = 0x8ab1;\n    P0.L = 0;\n', (ROOT / 'target/storage_probe_entry.s').read_text())
        self.assertIn("'-DXYF_STACK_END=%#xu' % stack_end", src)
        self.assertIn('Cold entry stack top differs from the mode layout', src)


class ProductFlowTest(unittest.TestCase):
    def test_file_boot_is_the_only_link_session(self):
        cold = (ROOT / 'target/cold.cpp').read_text()
        self.assertIn('xy_forever_file_boot();', cold)
        self.assertNotIn('xy_forever_menu_link', cold)          # the v2 USB service session

    def test_play_menu_belongs_to_file_boot(self):
        link = (ROOT / 'target/menu_link.cpp').read_text()
        self.assertNotIn('xy_forever_key_ui', link)
        ui = (ROOT / 'target/key_ui.cpp').read_text()
        self.assertNotIn('menu_link', ui)

    def test_product_mode_flags(self):
        self.assertEqual(builder.USB_SET_DEFAULT_MODES, builder.PRODUCT_MODES)   # Test 048: USB set default only
        self.assertIn('xyf-product', builder.WRITE_AUDIT_MODES)
        self.assertIn('xyf-product', builder.GC_MODES)
        src = (ROOT / 'xyf_build.py').read_text()
        self.assertNotIn('-DXYF_STATE_WRITE', src)       # no menu (SHIFT+PLAY) write
        f = builder.features('xyf-product')
        self.assertTrue(all(f[k] for k in ('usb', 'menu_link', 'remote_read', 'file_boot')))
        self.assertFalse(f['stage2'] or f['stage2_menu'])
        self.assertTrue(f['mtp'])                           # 091: MTP mode from the v3 menu


class LibraryBootTimingTest(unittest.TestCase):
    def test_a_waits_long_enough_for_b_to_admit_the_image(self):
        text = unit_text(ROOT / 'target/file_boot.cpp')
        body = text[text.index('bool role0_menu_boot(U32& bytes, U32& crc) {'):]
        self.assertIn('session.stage(product_role0_idle_ms, handshake_ms);', body[:body.index('session.begin(')])
        ms = int(re.search(r'product_role0_idle_ms = (\d+);', text).group(1))
        self.assertGreaterEqual(ms, 8000)          # Test 044: b's admit outlasted 3 s
        self.assertLessEqual(ms, 30000)            # below b's 30 s boot cap


class DryRunTest(unittest.TestCase):
    def test_dry_run_resets_instead_of_starting_the_os(self):
        fb = unit_text(ROOT / 'target/file_boot.cpp')
        end = fb[fb.index('extern "C" void xy_forever_file_boot() {'):]
        self.assertLess(end.index('hand_off(bytes, crc)'), end.index('usb_service::dry_run_finish(&r, sizeof(r));'))
        us = unit_text(ROOT / 'target/usb_service.cpp')
        finish = us[us.index('void dry_run_finish('):]
        finish = finish[:finish.index('\n}\n')]
        self.assertIn('marker_write(marker_reboot | carried_flags, 0);', finish)
        self.assertLess(finish.index('marker_write('), finish.index('reset_self();'))
        self.assertNotIn('write_state_slot', finish)
        self.assertIn('if (ok && mode == 1) ok = menu_link::usb_command(storage_server::dry_run_item);', us)
        fb = unit_text(ROOT / 'target/file_boot.cpp')
        self.assertIn('if (item == storage_server::dry_run_item) { usb_service::a_dry_run(); continue; }', fb)

    def test_dry_run_area_is_clear_of_staging_and_marker(self):
        header = (ROOT / 'target/usb_service.hpp').read_text()
        area = int(re.search(r'dry_run_area = (0x[0-9a-f]+)u', header).group(1), 16)
        size = int(re.search(r'dry_run_max_bytes = (0x[0-9a-f]+)u', header).group(1), 16)
        marker = int(re.search(r'reboot_marker = (0x[0-9a-f]+)u', header).group(1), 16)
        self.assertGreaterEqual(area, 0x8b820000 + 0x700000 + 0x10000)   # USB staging + read-back
        self.assertLessEqual(area + 8 + size, marker)


class HostInstallTest(unittest.TestCase):
    def test_host_installs_only_builds_with_the_usb_service(self):
        import json, tempfile, hashlib
        import xyf_host as host
        self.assertEqual(host.USB_MODES, ('xyf-usb', *builder.PRODUCT_MODES, builder.MTP_USB_MODE, builder.CRASH_TRIGGER_MODE))
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            data = bytes(70000)
            (d / 'candidate.ldr').write_bytes(data)
            (d / 'verification.json').write_text(json.dumps(dict(passed=True)))
            (d / 'manifest.json').write_text(json.dumps(dict(mode='xyf-boot', candidate_sha256=hashlib.sha256(data).hexdigest())))
            with self.assertRaisesRegex(ValueError, 'no USB service'):
                host.load_build(d)


if __name__ == '__main__':
    unittest.main()


class SetDefaultWriteTest(unittest.TestCase):
    """Test 048: the first storage write on hardware (USB SET_DEFAULT in the product build)."""

    def test_b_waits_for_a_write_behind_a_barrier(self):
        us = unit_text(ROOT / 'target/usb_service.cpp')
        body = us[us.index('U32 default_commit(U32 hash)'):]                  # set_default's A/B write
        body = body[:body.index('\n}\n')]
        order = ['storage_used = 1;', 'menu_link::usb_patience(write_patience_ms);',
                 'if (!menu_link::usb_command(storage_server::usb_mode_item)) code = P::s_link;',
                 'menu_link::usb_patience(0);']
        at = [body.index(n) for n in order]
        self.assertEqual(at, sorted(at))
        header = (ROOT / 'target/usb_service.hpp').read_text()
        self.assertIn('static const uint32_t write_patience_ms = 60000;', header)
        src = (ROOT / 'xyf_host.py').read_text()
        self.assertIn("timeout=P.ids.COMMANDS[P.SET_DEFAULT]['timeout_ms'] / 1000.0)", src)   # the spec's bound
        from xy_forever import usb_proto as P
        self.assertGreater(P.ids.COMMANDS[P.SET_DEFAULT]['timeout_ms'], 60000)   # outlasts b's patience (+ #65's wait)

    def test_a_write_is_not_idle(self):
        us = unit_text(ROOT / 'target/usb_service.cpp')
        a = us[us.index('void a_set_default('):]
        a = a[:a.index('\n}\n')]
        self.assertIn('storage.write_state_slot(', a)
        self.assertNotIn('guard_', a)
        fb = unit_text(ROOT / 'target/file_boot.cpp')
        w = fb[fb.index('usb_service::a_set_default(storage, a_collector.hash);'):]
        self.assertIn('session.touch(now());', w[:w.index('continue;')])        # the write is not idleness
        self.assertIn('b_patience_ms ? b_patience_ms : 2 * answer_ms)) b_lost = 1;', fb)

    def test_only_the_usb_write_is_compiled(self):
        src = (ROOT / 'xyf_build.py').read_text()
        self.assertIn("if mode in USB_SET_DEFAULT_MODES:\n        flags = [*flags, '-DXYF_USB_SET_DEFAULT=1']", src)
        self.assertNotIn('-DXYF_STATE_WRITE', src)
        cs = (ROOT / 'target/cold_storage.cpp').read_text()
        self.assertEqual(cs.count('#if defined(XYF_STATE_WRITE) || defined(XYF_USB_SET_DEFAULT)\n'), 2)


class PlainImageTest(unittest.TestCase):
    """Test 051: plain official os.ldr images (no report slot) boot from the library."""

    def test_admit_classifies_missing_slot_as_plain(self):
        fb = unit_text(ROOT / 'target/file_boot.cpp')
        a = fb[fb.index('U32 admit(Byte* image, U32 bytes) {'):]
        a = a[:a.index('\n}\n')]
        order = ['xy_forever_storage_image_plain = 0;', 'xyf_core::inspect(image, bytes, *policy, summary)',
                 'summary.entry != 0x11a00000u', 'trace_slot::replace(',
                 'slot.status == xyf_core::trace_slot::Status::missing_slot',
                 'xy_forever_storage_image_plain = 1;', 'result.library.flags |= 8u;',
                 'if (slot.status != xyf_core::trace_slot::Status::ok) return reason_report_slot;']
        at = [a.index(s) for s in order]
        self.assertEqual(at, sorted(at))
        # Only a MISSING slot is plain; a damaged one (duplicate, mismatch, ...) is still refused.
        self.assertEqual(a.count('missing_slot'), 1)

    def test_cold_boots_plain_images_unpatched_after_readmission(self):
        cold = (ROOT / 'target/cold.cpp').read_text()
        readmit = cold.index('xyf_core::inspect(reinterpret_cast<const Byte*>(0x8a000000u), n, policy, loaded)')
        skip = cold.index('if (from_storage && xy_forever_storage_image_plain == 1u) { patch = false; xy_forever_boot_source = 4u; }')
        patch = cold.index('if (patch && !xy_forever_patch_warm_snapshot(')
        self.assertLess(readmit, skip)
        self.assertLess(skip, patch)

    def test_official_images_pass_the_product_policy(self):
        from xy_forever.ldr import parse_ldr, validate_memory
        cold = (ROOT / 'target/cold.cpp').read_text()
        self.assertIn('{0x8ab00000u, XYF_STACK_END}, // module + stack ABOVE retained heap0 end', cold)
        allowed = [(0x08000000, 0x08040000), (0x11800000, 0x11c00000), (0x80000000, 0x90000000)]
        protected = [(0x0803e0c0, 0x0803e142), (0x11803000, 0x11804000), (0x8803d000, 0x8803d02a),
                     (0x88a346a6, 0x88a3481c), (0x88a34aec, 0x88a34d6c), (0x8a000000, 0x8a800000),
                     (0x8ab00000, builder.WIDE_STACK_END), (0x8b000000, 0x8b800000)]
        d = DECRYPTED
        images = [d / 'opxy_firmware_1_2_1/extracted/firmware/blackfin/bin/os.ldr',
                  d / 'opxy_firmware_1_2_5/firmware/blackfin/bin/os.ldr']
        if not all(p.exists() for p in images):
            self.skipTest('official images absent')
        # A mod image assembled on the device (scripts/mod_boot.py evidence), when present.
        images += sorted(SESSIONS.glob('*-mod-boot-*/assembled/os.ldr'))[:1]
        for p in images:
            records = parse_ldr(p.read_bytes())
            validate_memory(records, allowed, protected)
            self.assertEqual(records[-1].target, 0x11a00000)
            # no record of these images lands anywhere in the gap the module and stage 2 use.
            self.assertFalse([r for r in records if r.count and r.target < builder.FREE_GAP[1] and
                              builder.FREE_GAP[0] < r.target + r.count], p)

    def test_builder_and_decoder(self):
        self.assertEqual(builder.PLAIN_IMAGE_MODES, builder.PRODUCT_MODES)
        src = (ROOT / 'xyf_build.py').read_text()
        self.assertIn("if mode in PLAIN_IMAGE_MODES:\n        flags = [*flags, '-DXYF_PLAIN_IMAGES=1']", src)
        self.assertIn('plain=bool(lib[7] & 8)', (ROOT / 'xy_forever/census.py').read_text())


class ServiceScreenTest(unittest.TestCase):
    """Test 054: the USB service draws an XY Forever screen, only after a host HELLO."""

    def test_draws_only_after_hello_and_crc_gated(self):
        us = unit_text(ROOT / 'target/usb_service.cpp')
        say = us[us.index('void say(const char* a'):]
        say = say[:say.index('\n}\n')]
        self.assertIn('if (screen_on) key_ui::service_screen(a, b, c, d);', say)
        ui = (ROOT / 'target/key_ui.cpp').read_text()
        fn = ui[ui.index('bool service_screen(const char* a'):]
        fn = fn[:fn.index('\n}\n')]
        self.assertIn('crc32(spans[0].address, spans[0].bytes) == spans[0].crc', fn)
        self.assertLess(fn.index('if (service_checked != 1u) return false;'), fn.index('show(4, "");'))
        self.assertIn('{0x889ee800u, 0x1600u, 0x46ad94d9u},  // text console, glyphs, display post/vsync path', ui)
        for stock in (0x889eed4c, 0x889efa6a):                               # wait_dma, transfer inside span 0
            self.assertTrue(0x889ee800 <= stock < 0x889ee800 + 0x1600)

    def test_builder_flag(self):
        self.assertEqual(builder.SERVICE_SCREEN_MODES, builder.PRODUCT_MODES)
        src = (ROOT / 'xyf_build.py').read_text()
        self.assertIn("if mode in SERVICE_SCREEN_MODES:\n        flags = [*flags, '-DXYF_SERVICE_SCREEN=1']", src)


class MenuOverUsbTest(unittest.TestCase):
    """Test 058: the XY Forever menu with USB (SCREEN/KEY); v3 home answers every command."""

    def test_device_side(self):
        us = unit_text(ROOT / 'target/usb_service.cpp')
        pump = us[us.index('uint32_t home_pump() {'):]
        pump = pump[:pump.index('\n}\n')]
        self.assertIn('P::get32(payload) < 64u && P::get32(payload + 4) <= 1u', pump)
        rel = us[us.index('void menu_release() {'):]
        self.assertLess(rel.index('menu_usb = 0;'), rel.index('usb_stop();'))
        fb = unit_text(ROOT / 'target/file_boot.cpp')
        r1 = fb[fb.index('bool role1_menu_boot(U32& bytes) {'):]
        self.assertLess(r1.index('if (usb_service::menu_requested()) r.menu.play = 1;'), r1.index('xy_forever_key_ui();'))
        self.assertLess(r1.index('xy_forever_key_ui();'), r1.index('usb_service::menu_release();'))
        self.assertLess(r1.index('usb_service::menu_release();'), r1.index('session.transfer(reinterpret_cast<Byte*>(staging)'))
        ui = (ROOT / 'target/key_ui.cpp').read_text()
        self.assertIn('if (!r.play_snapshot && !xy_forever::usb_service::menu_requested() && !xy_forever::xyf_boot::menu_forced() &&', ui)
        self.assertIn('if (const uint32_t leave = xy_forever::usb_service::home_pump()) {', ui)
        self.assertIn('void inject_key(uint32_t key, uint32_t pressed) { key_injected = 1; on_key(key, pressed); key_injected = 0; }',
                      ui)                                                    # injected keys marked
        self.assertEqual(builder.USB_MENU_MODES, builder.PRODUCT_MODES)


class MenuOverUsbHostTest(unittest.TestCase):
    def test_host_commands(self):
        import xyf_host as host
        self.assertEqual(host.KEYS['play'], 51); self.assertEqual(host.KEYS['minus'], 53)
        self.assertEqual(host.KEYS['plus'], 54); self.assertEqual(host.KEYS['track1'], 6)
        from xy_forever import usb_proto as P
        self.assertEqual(P.KEY, 0x34)
        src = (ROOT / 'xyf_host.py').read_text()
        self.assertIn("payload = struct.pack('<I', 1) if args.dry_run else b''", src)   # no --menu
        self.assertNotIn('args.menu', src)
        self.assertIn("dev.call(P.KEY, struct.pack('<II', key, 1))", src)


class MenuOnFailureTest(unittest.TestCase):
    """Test 059: a default that can't boot opens the menu (CHECK before BOOT; a keeps the session)."""

    def setUp(self):
        self.fb = unit_text(ROOT / 'target/file_boot.cpp')

    def test_a_checks_with_storage_mounted(self):
        prep = self.fb[self.fb.index('U32 a_prepare('):]
        prep = prep[:prep.index('\n}\n')]
        order = ['read_image(storage, selection.path, bytes)', 'xyf::cold_library::matches(selection, bytes, crc)',
                 'admit(reinterpret_cast<Byte*>(staging), bytes)']
        at = [prep.index(s) for s in order]
        self.assertEqual(at, sorted(at))
        self.assertNotIn('storage.end()', prep)                               # the mount survives a failed check
        a = self.fb[self.fb.index('        const bool check = item == xyf_boot::item_check_default ||'):]
        a = a[:a.index('#else')]
        chk = a[a.index('if (check) {'):a.index('            continue;\n        }\n')]
        self.assertIn('session.freeze_blob(reinterpret_cast<const Byte*>(xyf_boot::status_buffer), sizeof(s));', chk)
        self.assertNotIn('storage.end()', chk)
        self.assertNotIn('refuse()', chk)                                      # a failed check never ends the session
        boot = a[a.index('U32 reason = a_prepared == index'):]
        self.assertLess(boot.index('storage.end()'), boot.index('session.freeze_image(reinterpret_cast<const Byte*>(staging)'))
        self.assertLess(boot.index('r.storage_safe = storage.safe_to_handoff()'), boot.index('session.freeze_image('))

    def test_b_flow(self):
        r1 = self.fb[self.fb.index('bool role1_menu_boot(U32& bytes) {'):]
        r1 = r1[:r1.index('#else\n    if (!b_lost && r.menu.play) {')]
        order = ['b_check(session, xyf_boot::item_check_default)', 'b_failed = 1; b_note(true, reason); r.menu.play = 1;',
                 'while (!b_lost && !boot_item) {', 'b_fetch_catalog(session)', 'xy_forever_key_ui();',
                 'b_check(session, b_choice == 2 ? xyf_boot::item_check_entry + b_entry :',
                 'usb_service::menu_release();', 'if (!b_lost && boot_item) {', 'b_send(session, boot_item)']
        at = [r1.index(s) for s in order]
        self.assertEqual(at, sorted(at))
        self.assertIn('if (r.admit_reason != reason_ok) break;            // not admitted: never release', r1)
        self.assertIn('now(), xyf_boot::role1_menu_cap_ms);           // Test 059', self.fb)

    def test_menu_waits_and_items(self):
        ui = (ROOT / 'target/key_ui.cpp').read_text()
        self.assertIn('if (!xy_forever::xyf_boot::menu_forced() && ms_since(r.start_cycles) > last_key_ms + idle_timeout_ms) {', ui)
        hdr = (ROOT / 'target/xyf_boot.hpp').read_text()
        self.assertIn('item_check_default = 0x13, item_check_entry = 0x30;', hdr)
        self.assertIn('static_assert(sizeof(CheckStatus) == 120, "CheckStatus layout");', hdr)
        # 0x30..0x3f collides with nothing (entries 0x20..0x2f, set default 0x40.., id bytes 0x60..)
        self.assertIn('item_boot_entry = 0x20', hdr)
        self.assertIn('if (!b_lost && b_notice[0]) return b_notice;', self.fb)
        self.assertEqual(builder.MENU_ON_FAILURE_MODES, builder.PRODUCT_MODES)


class BootModelTest(unittest.TestCase):
    """Test 060 (project rule): normal boots never wait for a computer; PLAY opens the menu with
    USB; our reboots return home (v3; the v2 held service and its menu HOLD are gone)."""

    def test_home_only_after_our_reboots(self):
        us = unit_text(ROOT / 'target/usb_service.cpp')
        hb = us[us.index('bool home_begin() {'):]
        self.assertIn('r.present = presence() == 3 ? 3u : 0u;', hb[:hb.index('\n}\n')])
        self.assertEqual(builder.SERVICE_ON_MARKER_MODES, builder.PRODUCT_MODES)

    def test_play_menu_usb_and_hold(self):
        us = unit_text(ROOT / 'target/usb_service.cpp')
        start = us[us.index('void menu_start_usb() {'):]
        start = start[:start.index('\n}\n')]
        self.assertLess(start.index('identity()'), start.index('usb_start();'))
        home = us[us.index('void home_start() {'):]
        self.assertIn('menu_start_usb();', home[:home.index('\n}\n')])
        fb = unit_text(ROOT / 'target/file_boot.cpp')
        r1 = fb[fb.index('bool role1_menu_boot(U32& bytes) {'):]
        self.assertLess(r1.index('usb_service::home_start();'), r1.index('xy_forever_key_ui();'))
        hdr = (ROOT / 'target/xyf_boot.hpp').read_text()
        self.assertNotIn('item_service', hdr + fb)                           # the menu HOLD into the v2 service


class BootTransferBoundTest(unittest.TestCase):
    def test_bounds(self):
        fb = unit_text(ROOT / 'target/file_boot.cpp')
        self.assertIn('while (session.blob_pending() && now() - t < 2000u) session.poll(now());', fb)
        self.assertIn('now() - r.menu.boot_ms < xyf_boot::boot_transfer_ms) {   // Test 061: own bound', fb)
        self.assertIn('boot_transfer_ms = 30000;', (ROOT / 'target/xyf_boot.hpp').read_text())


class KeepUsbTest(unittest.TestCase):
    """Test 062: USB never stops and restarts within one boot; check reply size; progress."""

    def test_device(self):
        us = unit_text(ROOT / 'target/usb_service.cpp')
        self.assertIn('bool menu_requested() { return menu_usb == 1u || menu_usb == 2u; }', us)
        self.assertIn('if (menu_usb == 3u) { menu_usb = 1; return; }      // Test 062: USB kept from the service', us)
        fb = unit_text(ROOT / 'target/file_boot.cpp')
        self.assertIn('session.image_size() < sizeof(xyf_boot::CheckStatus)', fb)
        self.assertIn('usb_service::progress("Checking the default firmware...", "");', fb)


class RebootTargetsTest(unittest.TestCase):
    """Test 064 (project rule): our REBOOT goes to the service, the OS or the boot menu (with USB),
    from the service and from the menu; PLAY at power-on = the boot menu with USB."""

    def test_device(self):
        hdr = (ROOT / 'target/usb_service.hpp').read_text()
        self.assertIn('static const uint32_t marker_menu = 0x400u;', hdr)
        self.assertIn('reboot_service = 0, reboot_os = 1, reboot_menu = 2;', hdr)
        us = unit_text(ROOT / 'target/usb_service.cpp')
        # v3: REBOOT from home goes to the OS (no marker) or home again (service and menu alike).
        pump = us[us.index('uint32_t home_pump() {'):]
        pump = pump[:pump.index('\n}\n')]
        self.assertIn('if (target > reboot_menu) status_only(type, seq, P::s_bad_request);', pump)
        hr = us[us.index('void home_reboot(U32 target) {'):]
        hr = hr[:hr.index('\n}\n')]
        self.assertIn('if (target == reboot_os) marker_clear();\n    else marker_write(marker_reboot | marker_menu | carry, 0);', hr)
        self.assertIn("'-DXYF_SERVICE_ON_MARKER_ONLY=1', '-DXYF_REBOOT_TARGETS=1'", (ROOT / 'xyf_build.py').read_text())

    def test_host(self):
        src = (ROOT / 'xyf_host.py').read_text()
        self.assertIn("reboot.add_argument('--to', choices=('service', 'os', 'menu'), default='service',", src)
        self.assertIn("target = {'service': 0, 'os': 1, 'menu': 2}[args.to]", src)


class OneSessionTest(unittest.TestCase):
    """Test 065 (boot model v3): one link session per boot; the menu is home for every USB command."""

    def test_a_routes_and_shares_the_mount(self):
        ps = (ROOT / 'target/peer_services.cpp').read_text()
        self.assertIn('if(!agreed_&&staged_&&rpc_&&serve_&&!blob_&&!pending_&&server_.phase()==xyf_core::peer::Phase::waiting){', ps)
        ss = ''.join((ROOT / 'target' / n).read_text()
                      for n in ('storage_server.cpp', 'storage_server_engine.ipp'))
        m = ss[ss.index('bool mount(cold_storage::Stock121ReadOnlySession& storage) {'):]
        m = m[:m.index('\n}\n')]
        self.assertLess(m.index('if (adopted) {'), m.index('storage.begin_fresh('))      # never a second mount
        u = ss[ss.index('void unmount(cold_storage::Stock121ReadOnlySession& storage) {'):]
        u = u[:u.index('\n}\n')]
        self.assertLess(u.index('if (adopted) { mounted = 0; return; }'), u.index('storage.end()'))
        fb = unit_text(ROOT / 'target/file_boot.cpp')
        r0 = fb[fb.index('bool role0_menu_boot(U32& bytes, U32& crc) {'):]
        self.assertLess(r0.index('storage_server::install(session, storage);'), r0.index('session.begin('))
        # 191: adopted also when the mount failed (op 8 answers a failure until a retry mounts)
        self.assertIn('storage_server::adopt();\n        a_note_mount(storage, r.storage_begin, now() - mount_start);', r0)
        self.assertIn('item == storage_server::file_write_item) { session.set_rpc(true); continue; }', r0)
        self.assertIn('session.set_rpc(false);', r0)
        self.assertEqual(fb.count('if (mounted) { a_close_files(storage); r.storage_end = U32(storage.end()); mounted = false; }'), 5)
        # every way out closes the engine (window and handles) before the shared mount ends.
        ss_files = ss[ss.index('void close_files(cold_storage::Stock121ReadOnlySession& storage) {'):]
        self.assertLess(ss_files.index('close_engine(storage);'), ss_files.index('mounted = 0;'))
        # RAM-lease release is covered through the production transport by test_a_install_stage.

    def test_b_home(self):
        us = unit_text(ROOT / 'target/usb_service.cpp')
        hp = us[us.index('uint32_t home_pump() {'):]
        hp = hp[:hp.index('\n}\n')]
        for needed in ('P::t_install', 'P::t_file_write', 'P::t_read_file', 'P::t_put_begin', 'P::t_set_default',
                       'P::t_screen', 'P::t_key', 'P::t_continue', 'P::t_reboot', 'P::t_hold', 'P::t_status'):
            self.assertIn(needed, hp)
        self.assertIn('else if (!home_greeted) status_only(type, seq, P::s_state);', hp)
        hr = us[us.index('void home_reboot(U32 target) {'):]
        hr = hr[:hr.index('\n}\n')]
        self.assertNotIn('P::t_guard', hp)
        order = ['menu_link::usb_command(storage_server::reboot_item);', 'menu_release();',
                 'if (target == reboot_os) marker_clear();', 'reset_self();']
        at = [hr.index(s) for s in order]
        self.assertEqual(at, sorted(at))
        hb = us[us.index('bool home_begin() {'):]
        hb = hb[:hb.index('\n}\n')]
        self.assertIn('if (r.crash_restarts >= max_crash_restarts) {', hb)
        ml = (ROOT / 'target/menu_link.cpp').read_text()
        self.assertIn('bool tick() { return xyf_boot::tick(); }               // Test 065', ml)
        ui = (ROOT / 'target/key_ui.cpp').read_text()
        self.assertIn('if (const uint32_t leave = xy_forever::usb_service::home_pump()) {', ui)
        self.assertIn('// Test 066 (project rule): the boot menu never times out.\n            if (false) {', ui)
        self.assertEqual(builder.ONE_SESSION_MODES, ('xyf-product',))
        # Bug 4: the menu stays up forever without restarting: v3 link sessions have no cap
        # (wrap-safe xyf_clock) and home no longer reboots itself on a timer.
        xb = (ROOT / 'target/xyf_boot.hpp').read_text()
        v3 = xb[xb.index('#if defined(XYF_ONE_SESSION)\n// v3: no session cap'):]
        v3 = v3[:v3.index('#else')]
        self.assertIn('role0_cap_ms = 0xffffffffu;', v3)
        self.assertIn('role1_menu_cap_ms = 0xffffffffu', v3)
        self.assertNotIn('home_renew_ms', us + (ROOT / 'target/usb_service.hpp').read_text())
        ps = (ROOT / 'target/peer_services.cpp').read_text()
        self.assertIn('if(timeout_!=no_cap&&diagnostic_.elapsed_ms>=timeout_)return fail(Result::timeout);', ps)
        for name in ('file_boot', 'usb_service'):
            source = unit_text(ROOT / 'target' / (name + '.cpp'))
            self.assertIn('now() { return xyf::clock::ms() - start_ms; }', source)
            self.assertNotIn('* 21u) >> 13', source)


class OneSessionWriteTest(unittest.TestCase):
    """Test 066: file writes after reads in the one link session (no fresh boot needed)."""

    def test_flow(self):
        us = unit_text(ROOT / 'target/usb_service.cpp')
        fw = us[us.index('void file_write(uint8_t seq'):]
        fw = fw[:fw.index('\n}\n')]
        order = ['(code = enter_writes()) != P::s_ok', 'file_client::write_file(', 'writes_due = 1; writes_ms = now();']
        at = [fw.index(s) for s in order]
        self.assertEqual(at, sorted(at))
        # #3: A's XY Forever check (a fresh command item) comes before op 9, whatever read_enabled latched.
        ew = us[us.index('U32 enter_writes() {'):]
        ew = ew[:ew.index('\n}\n')]
        order = ['if (mounted == file_client::mounted_stale) {', 'if (!a_serving()) return P::s_link;',
                 'file_client::release(mounted);', 'menu_link::usb_command(storage_server::file_write_item)']
        at = [ew.index(s) for s in order]
        self.assertEqual(at, sorted(at))
        self.assertNotIn('read_enabled', ew)
        prep = us[us.index('xyf_core::U32 mtp_prepare(void*) {'):]
        prep = prep[:prep.index('\n}\n')]
        self.assertLess(prep.index('mounted != file_client::mounted_stale &&'), prep.index('a_serving()'))
        self.assertLess(prep.index('a_serving()'), prep.index('file_client::mount_ready(mounted)'))
        ss = ''.join((ROOT / 'target' / n).read_text()
                      for n in ('storage_server.cpp', 'storage_server_engine.ipp'))
        self.assertIn('if ((used && !adopted) || !mount(storage)) return false;   // v3: the adopted mount is reusable', ss)
        self.assertIn('if (!window_open && engine == entry_write) engine = entry_read;   // Test 066: one write per item', ss)
        # op 9 fails while the window is open or when its read-only reopen failed (as 154).
        self.assertIn('const bool ok = !window_open && (!had || window_result == U32(cold_storage::Result::ok));', ss)
        self.assertIn('if (!seen || seen == engine_latched || engine == entry_run) return;', ss)


class NoEmbeddedOsTest(unittest.TestCase):
    """Project rule: the product carries no embedded OS."""

    def test_outer_stream_has_no_payload(self):
        from xy_forever import outer
        module = bytes(range(256)) * 4
        candidate = outer.package(module, None, 0x8ab00000)
        checks = outer.verify_outer(candidate, module, None, 0x8ab00000)
        self.assertEqual(checks['records'], 4)
        self.assertFalse(checks['embedded_os'])
        with self.assertRaises(ValueError):                 # a payload-carrying stream is not this layout
            outer.verify_outer(outer.package(module, b'x' * 64, 0x8ab00000), module, None, 0x8ab00000)
        self.assertEqual(builder.NO_EMBEDDED_MODES, ('xyf-product',))

    def test_cold_path_halts_with_a_message_instead_of_a_fallback(self):
        cold = (ROOT / 'target/cold.cpp').read_text()
        self.assertIn('descriptor.payload_bytes != 0', cold)
        self.assertIn('if (!from_storage) { xy_forever_no_image(); stop(); }', cold)
        fb = unit_text(ROOT / 'target/file_boot.cpp')
        handler = fb[fb.index('extern "C" void xy_forever_no_image() {'):]
        self.assertIn('if (result.role != 1) return;', handler)
        self.assertIn('"Internal link lost."', handler)
        self.assertIn('"Turn the OP-XY off and on."', handler)
        # The dry run still returns home before any of this.
        end = fb[fb.index('extern "C" void xy_forever_file_boot() {'):fb.index('extern "C" void xy_forever_no_image()')]
        self.assertIn('usb_service::dry_run_finish(&r, sizeof(r));', end)


class MtpExecutorTest(unittest.TestCase):
    """091/097: a serves the engine's run entry from the boot session (the self-test reaches it)."""

    def test_flow(self):
        fb = unit_text(ROOT / 'target/file_boot.cpp')
        a = fb[fb.index('if (item == storage_server::engine_run_item) {'):]
        a = a[:a.index('#endif')]
        self.assertLess(a.index('session.set_rpc(true);'), a.index('storage_server::run_engine(session, storage);'))
        ss = ''.join((ROOT / 'target' / n).read_text()
                      for n in ('storage_server.cpp', 'storage_server_engine.ipp'))
        run = ss[ss.index('void run_engine('):]
        run = run[:run.index('\n}\n')]
        self.assertIn('session.touch(xyf_boot::clock_ms());', run)       # b sends no keepalives in the run entry
        op = ss[ss.index('bool open_window('):ss.index('void close_engine(')]
        self.assertIn('if ((used && !adopted) || !mount(storage)) return false;', op)
        self.assertIn('storage.reopen_read_only()', op)
        cl = ss[ss.index('void close_engine('):ss.index('void serve_engine(')]
        self.assertIn('if (adopted) window_result = U32(storage.reopen_read_only());', cl)

    def test_menu_row(self):
        """The stock MTP mode is gone; 188: so is the MTP mode row (MTP is always on with the composite)."""
        ui = (ROOT / 'target/key_ui.cpp').read_text()
        self.assertNotIn('mtp_mode', ui)
        self.assertNotIn('mtp_mode', (ROOT / 'target/xyf_menu.hpp').read_text())
        self.assertNotIn('"MTP mode"', (ROOT / 'target/xyf_menu.cpp').read_text())


class PeekTest(unittest.TestCase):
    """093: read-only PEEK of stock DDR/L2 code, never L1 instruction memory."""

    def test_bounds(self):
        us = unit_text(ROOT / 'target/usb_service.cpp')
        body = us[us.index('bool peek_allowed(U32 address, U32 bytes) {'):]
        body = body[:body.index('\n}\n')]
        self.assertIn('bytes && bytes <= read_range_max', body)
        self.assertIn('within(address, bytes, 0x88000000u, 0x88b00000u)', body)
        self.assertIn('within(address, bytes, 0x08000000u, 0x08040000u)', body)
        self.assertIn('within(address, bytes, 0x11b00000u, 0x11b02000u)', body)   # L1 data only
        self.assertNotIn('0x11a', body)                 # L1 instruction: data loads from it stall
        self.assertIn('within(address, bytes, 0x8b810000u, 0x8b811000u)', body)   # B's catalog buffer
        self.assertIn('bool within(U32 address, U32 bytes, U32 start, U32 end) { return address >= start && address <= end - bytes; }', us)
        hp = us[us.index('uint32_t home_pump() {'):]
        self.assertIn('else if (type == P::t_peek) peek(seq, payload, length);', hp)

    def test_mmr_reads(self):
        """095: SEC/core registers by 32-bit reads only, never CSID (0x2000441c) or the core SID."""
        us = unit_text(ROOT / 'target/usb_service.cpp')
        body = us[us.index('bool mmr_allowed(U32 address, U32 bytes) {'):]
        body = body[:body.index('\n}\n')]
        self.assertIn('!(address & 3u) && !(bytes & 3u)', body)
        self.assertIn('within(address, bytes, 0x20004400u, 0x20004408u)', body)   # CCTL/CSTAT only
        self.assertIn('within(address, bytes, 0x20004800u, 0x20004a80u)', body)   # sources 0..0x4f only
        self.assertNotIn('0x20004c00', body)            # 095: reading SCTL up to 127 hung b
        self.assertNotIn('0x1fc02118', body)
        self.assertNotIn('0x1fc02000', body)
        peek = us[us.index('void peek(uint8_t seq'):]
        peek = peek[:peek.index('\n}\n')]
        self.assertIn('if (mmr) for (U32 i = 0; i != bytes; i += 4) P::put32(files + 8 + i, word(address + i));', peek)


class MtpSelfTestTest(unittest.TestCase):
    """097: the storage self-test from b's main loop; a leaves the run entry on engine_leave_item."""

    def test_leave(self):
        ss = ''.join((ROOT / 'target' / n).read_text()
                      for n in ('storage_server.cpp', 'storage_server_engine.ipp'))
        run = ss[ss.index('void run_engine(Stock121PeerSession& session'):]
        v3 = run[:run.index('\n}\n')]
        self.assertIn('if (item == engine_leave_item) {', v3)
        # the window must be closed before the run entry ends
        self.assertLess(v3.index('close_engine(storage);'), v3.index('engine = entry_read;'))
        self.assertIn('if (window_open) continue;', v3)
        fb = unit_text(ROOT / 'target/file_boot.cpp')
        self.assertIn('handled = session.commands_seen();          // the leave item was consumed in run_engine', fb)
        hpp = (ROOT / 'target/storage_server.hpp').read_text()
        self.assertIn('static const uint32_t engine_leave_item = 11;', hpp)
        items = [int(v) for v in re.findall(r'_item = (\d+);', hpp)]
        self.assertEqual(len(items), len(set(items)))

    def test_selftest_order(self):
        ss = unit_text(ROOT / 'target/usb_service.cpp')
        body = ss[ss.index('bool mtp_do(U32 n, MtpSelfTest& t, uint8_t* out) {'):]
        body = body[:body.index('\n}\n')]
        # B's file client, every request bounded (no stock posix client call).
        order = ['C::mount()', 'C::statfs(', 'C::stat(', 'C::opendir(', 'C::readdir(', 'C::closedir(',
                 'C::open(', 'C::read(', 'C::close(', 'C::unmount()']
        at = [body.index(a) for a in order]
        self.assertEqual(at, sorted(at))
        self.assertNotIn('C::write(', body)             # reads only
        self.assertNotIn('stock<', body)
        us = unit_text(ROOT / 'target/usb_service.cpp')
        t = us[us.index('void mtp_test(uint8_t seq'):]
        t = t[:t.index('\n}\n')]
        self.assertLess(t.index('mtp_enter()'), t.index('mtp_selftest(t, hold'))
        self.assertLess(t.index('mtp_selftest(t, hold'), t.index('mtp_leave(t)'))
        enter = ss[ss.index('U32 mtp_enter() {'):]
        self.assertLess(enter.index('file_client::release(mounted)'), enter.index('storage_server::engine_run_item'))
        self.assertNotIn('guard', t)                    # there is no guard
        from xy_forever import usb_proto
        self.assertEqual(usb_proto.MTP_TEST, 0x07)

    def test_usb_notice(self):
        us = unit_text(ROOT / 'target/usb_service.cpp')
        start = us[us.index('void menu_start_usb() {'):]
        start = start[:start.index('\n}\n')]
        self.assertLess(start.index('usb_off = identity()'), start.index('usb_start();'))
