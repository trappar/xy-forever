"""XY Forever product build (--xyf-product): the wide cold layout, the product mode's features,
the host's build checks and the payload-free outer stream. No device access."""
import unittest
from pathlib import Path

from xy_forever import xyf_build as builder
from xy_forever.paths import DECRYPTED, SESSIONS


class ProductLayoutTest(unittest.TestCase):
    def test_only_the_product_modes_are_wide(self):
        for mode in builder.MODES:
            wide = builder.product_kind(mode) in builder.PRODUCT_MODES     # the MTP USB test build is the product's
            if wide:
                self.assertEqual(builder.layout(mode), (0x8ab00000, 0x8ab80000, 0x8ab84000), mode)
            else:
                self.assertIn(mode, ('xyf-stage2', 'xyf-stage2-menu'))
                self.assertEqual(builder.layout(mode), (0x8ab00000, 0x8ab0e000, 0x8ab12000), mode)

    def test_one_product_mode(self):
        # xyf-product-v2 (build 064's two-session flow) was removed.
        self.assertEqual(builder.PRODUCT_MODES, ('xyf-product',))
        self.assertNotIn('xyf-product-v2', builder.MODES)
        for name in ('WIDE', 'FILE_WRITE', 'PLAIN_IMAGE', 'SERVICE_SCREEN', 'USB_MENU', 'MENU_ON_FAILURE',
                     'SERVICE_ON_MARKER', 'USB_SET_DEFAULT'):
            self.assertIs(getattr(builder, name + '_MODES'), builder.PRODUCT_MODES, name)
        self.assertEqual(builder.ONE_SESSION_MODES, ('xyf-product',))

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


class ProductFlowTest(unittest.TestCase):
    def test_product_mode_flags(self):
        self.assertEqual(builder.USB_SET_DEFAULT_MODES, builder.PRODUCT_MODES)   # Test 048: USB set default only
        self.assertIn('xyf-product', builder.WRITE_AUDIT_MODES)
        self.assertIn('xyf-product', builder.GC_MODES)
        f = builder.features('xyf-product')
        self.assertTrue(all(f[k] for k in ('usb', 'menu_link', 'remote_read', 'file_boot')))
        self.assertFalse(f['stage2'] or f['stage2_menu'])
        self.assertTrue(f['mtp'])                           # 091: MTP mode from the v3 menu


class HostInstallTest(unittest.TestCase):
    def test_host_installs_only_builds_with_the_usb_service(self):
        import json, tempfile, hashlib
        from xy_forever import xyf_host as host
        self.assertEqual(host.USB_MODES, ('xyf-usb', *builder.PRODUCT_MODES, builder.MTP_USB_MODE, builder.CRASH_TRIGGER_MODE))
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            data = bytes(70000)
            (d / 'candidate.ldr').write_bytes(data)
            (d / 'verification.json').write_text(json.dumps(dict(passed=True)))
            (d / 'manifest.json').write_text(json.dumps(dict(mode='xyf-boot', candidate_sha256=hashlib.sha256(data).hexdigest())))
            with self.assertRaisesRegex(ValueError, 'no USB service'):
                host.load_build(d)


class PlainImageTest(unittest.TestCase):
    """Test 051: plain official os.ldr images (no report slot) boot from the library."""

    def test_official_images_pass_the_product_policy(self):
        from xy_forever.ldr import parse_ldr, validate_memory
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


class MenuOverUsbHostTest(unittest.TestCase):
    def test_host_commands(self):
        from xy_forever import xyf_host as host
        self.assertEqual(host.KEYS['play'], 51); self.assertEqual(host.KEYS['minus'], 53)
        self.assertEqual(host.KEYS['plus'], 54); self.assertEqual(host.KEYS['track1'], 6)
        from xy_forever import usb_proto as P
        self.assertEqual(P.KEY, 0x34)


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


if __name__ == '__main__':
    unittest.main()
