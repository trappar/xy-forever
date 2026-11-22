"""Test018 key UI: decoder, span table and call-target audit; no device execution."""

import re
import struct
import unittest
from pathlib import Path

from xy_forever.census import (
    KEY_UI_DISPLAY_FIELDS,
    KEY_UI_FIELDS,
    KEY_UI_MAGIC,
    KEYS,
    boot_key_snapshot,
    held_keys,
    key_ui,
)
from xy_forever.source_text import unit_text
from xy_forever.xyf_build import KEY_UI_CALLS, call_targets, key_ui_spans

ROOT = Path(__file__).resolve().parents[1]


def regions_and_dump(blocks):
    """blocks: [(address, bytes)] -> (regions, dump) as census.parse returns."""
    dump, regions = bytearray(), []
    for address, data in blocks:
        regions.append(dict(address=address, bytes=len(data), offset=len(dump)))
        dump += data
    return regions, bytes(dump)


def key_frame(keys, kind=0x42, length=0x0A):
    bitmap = bytearray(10)
    for k in keys:
        bitmap[k // 8] |= 1 << (k % 8)
    return bytes([0x4B, 0xEE, kind, length]) + bytes(bitmap)


class KeyUiTest(unittest.TestCase):
    def test_key_enum_matches_bootloader_indices(self):
        self.assertEqual(len(KEYS), 72)  # KeyCount
        self.assertEqual((KEYS[51], KEYS[24], KEYS[6], KEYS[55]), ('Play', 'Usb', 'Track1', 'Shift'))

    def test_boot_key_snapshot_reads_stock_reply(self):
        frame = key_frame([51])
        self.assertEqual(frame[4 + 6], 0x08)  # Test017 run2: byte 10 = 0x08
        regions, dump = regions_and_dump([(0x8807CE7E, frame)])
        self.assertEqual(boot_key_snapshot(regions, dump), ['Play'])
        regions, dump = regions_and_dump([(0x8807CE7E, key_frame([], kind=0x05))])
        self.assertIsNone(boot_key_snapshot(regions, dump))
        self.assertEqual(held_keys(key_frame([24, 13])[4:]), ['EncClick4', 'Usb'])

    def test_key_ui_block_decodes(self):
        self.assertEqual(len(KEY_UI_FIELDS) + 24, 0xC0 // 4)
        words = [0] * 48
        values = dict(
            magic=KEY_UI_MAGIC,
            version=1,
            role=1,
            stages=0b11111111,
            play_snapshot=1,
            events=2,
            exit_reason=1,
            link_state=0x0101,
        )
        for name, value in values.items():
            words[KEY_UI_FIELDS.index(name)] = value
        frame = key_frame([51])
        for i, name in enumerate(('frame_header', 'bitmap0', 'bitmap1', 'bitmap2')):
            words[KEY_UI_FIELDS.index(name)] = struct.unpack_from('<I', frame + bytes(2), i * 4)[0]
        words[24] = 51 | 1 << 8 | 1200 << 16
        words[25] = 51 | 0 << 8 | 1500 << 16
        regions, dump = regions_and_dump([(0x11900000, bytes(64)), (0x8AB01BE0, struct.pack('<48I', *words))])
        out = key_ui(regions, dump)
        self.assertEqual(out['exit_reason'], 'play')
        self.assertEqual(out['stages'][-1], 'closed')
        self.assertEqual(out['snapshot_keys'], ['Play'])
        self.assertEqual(out['event_log'], ['Play down @1200ms', 'Play up @1500ms'])
        self.assertEqual((out['link_open'], out['link_greeted']), (1, 1))
        self.assertIsNone(key_ui(*regions_and_dump([(0x11900000, bytes(0xC0))])))

    def test_key_ui_v3_block_decodes_display_fields(self):
        fields = KEY_UI_FIELDS + KEY_UI_DISPLAY_FIELDS
        words = [0] * 48
        values = dict(
            magic=KEY_UI_MAGIC,
            version=3,
            role=1,
            events=1,
            exit_reason=1,
            presents=7,
            display_entry=0x0101,
            present_skips=0x10000,
            dma_stat_entry=0x1,
        )
        for name, value in values.items():
            words[fields.index(name)] = value
        words[len(fields)] = 51 | 1 << 8 | 900 << 16
        out = key_ui(*regions_and_dump([(0x8AB01C24, struct.pack('<48I', *words))]))
        self.assertEqual(out['event_log'], ['Play down @900ms'])
        self.assertEqual((out['presents'], out['smc_dma_active_entry'], out['frame_flag_entry']), (7, 1, 1))
        self.assertEqual((out['dma_not_idle'], out['stale_busy_cleared']), (0, 1))

    def test_present_uses_stock_synchronous_display_path(self):
        text = unit_text(ROOT / 'target/key_ui.cpp')  # present() is in its part key_ui_display.ipp
        body = text[text.index('void present() {') : text.index('\n}\n', text.index('void present() {'))]
        order = [
            body.index(s) for s in ('dma_idle(', 'wait_dma();', 'draw_screen(', 'flush_framebuffer(', 'transfer();')
        ]
        self.assertEqual(order, sorted(order))  # as stock 88a1de7c: wait 889eed4c, draw, transfer 889efa6a
        for console in ('889eeab2', '889eea28'):  # never the stock text console
            self.assertNotIn(console, text)
            self.assertNotIn(console, KEY_UI_CALLS)

    def test_header_layout_matches_decoder(self):
        text = (ROOT / 'target/key_ui.hpp').read_text()
        body = text[text.index('struct Result {') : text.index('};', text.index('struct Result {'))]
        names = []
        for decl in re.findall(r'uint32_t ([^;]+);', body):
            for name in decl.split(','):
                name = name.strip()
                names += (
                    [name.split('[')[0] + str(i) for i in range(3)]
                    if name.startswith('bitmap[')
                    else []
                    if name.startswith('event[')
                    else [name]
                )
        self.assertEqual(names, KEY_UI_FIELDS + KEY_UI_DISPLAY_FIELDS)
        self.assertIn('max_events = 20;', text)
        self.assertEqual(len(names) + 20, 0xC0 // 4)

    def test_span_table_covers_every_stock_call(self):
        spans = key_ui_spans()
        self.assertEqual(len(spans), 5)
        for call in KEY_UI_CALLS:
            self.assertTrue(any(a <= int(call, 16) < a + n for a, n, _ in spans), call)
        for address, _, _ in spans:
            self.assertFalse(0x11A00000 <= address < 0x11A0C000, 'never L1 instruction memory')

    def test_ms_conversion_matches_400_mhz_clock(self):
        # Test018 regression: *41 >> 4 gave microseconds (20 s timeout fired at 20 ms).
        text = unit_text(ROOT / 'target/key_ui.cpp')  # present() is in its part key_ui_display.ipp
        self.assertIn(
            'uint32_t ms_since(uint32_t start) { return xyf::clock::ms_from_units(cycles_div1024() - start); }', text
        )
        clock = (ROOT / 'target/xyf_clock.hpp').read_text()  # ms_from_units: (units >> 13) * mul + ...
        mul, shift = map(int, re.search(r'return \(units >> (\d+)\) \* (\d+)u', clock).groups()[::-1])
        units_per_second = 400_000_000 // 1024  # timer rate word 0x88a2ef28 (Test017)
        for seconds in (1, 20, 300):
            ms = (units_per_second * seconds * mul) >> shift  # xyf_clock: no 32-bit overflow
            self.assertAlmostEqual(ms / (seconds * 1000), 1, delta=0.005)

    def test_menu_busy_guard_matches_the_simulator(self):
        """key_ui and the simulator's mirror (sim/devices.cpp) hold the same menu actions
        while a job owns the write window: Continue, Boot without mods, Restore Open-XY and the first row's apply
        (#197), the actions that boot (`boots`)."""
        expected = {'continue_boot', 'safe_mode', 'restore', 'apply'}
        for path in ('target/key_ui.cpp', 'sim/menu_keys.ipp'):
            text = (ROOT / path).read_text()
            at = text.index('menu_busy()')
            head = text[text.rindex('const bool boots =', 0, at) : at]
            self.assertEqual(set(re.findall(r'Action::(\w+)', head)), expected, path)

    def test_simulator_mirrors_every_menu_action(self):
        """The simulator does not compile key_ui.cpp: sim/devices.cpp handles each menu action key_ui does (but
        MTP mode, which the simulator now runs as the MTP session, while key_ui has no session yet). Both run the Mod memory row's PLAY through mods_io::memory_flip, have the
        Settings page read the mod packages (prepare, pump while it is shown), and no longer turn knob rows by a
        stored direction (the demo setting left)."""
        actions = {}
        for path in ('target/key_ui.cpp', 'sim/devices.cpp'):
            text = (ROOT / path).read_text()
            if path == 'sim/devices.cpp':  # its part: the keys (dispatch)
                text += (ROOT / 'sim/menu_keys.ipp').read_text()
            actions[path] = set(re.findall(r'Action::(\w+)', text))
            self.assertIn('change_setting', actions[path], path)
            self.assertIn('mods_io::memory_flip()', text, path)
            self.assertIn('mods_io::prepare()', text, path)
            self.assertNotIn('settings::direct(', text, path)
            self.assertNotIn('knob_reversed', text, path)
        self.assertEqual(actions['target/key_ui.cpp'] - actions['sim/devices.cpp'], set())

    def test_call_audit_is_straight_line_only(self):
        listing = '\n'.join(
            [
                '8ab01000 <_wrapper>:',
                '8ab01000:\t4a e1 a3 88 \tp2.h = 0x88a3;\t\t/* (-30557)\tp2=0x88a30000 */',
                '8ab01004:\t0a e1 6a 42 \tp2.l = 0x426a;\t\t/* (17002)\tp2=0x88a3426a */',
                '8ab01008:\t03 1c       \tif cc jump 0x8ab01010 (bp);',
                '8ab0100a:\t52 00       \tjump (p2);',
                '8ab0100c:\t4a ac       \tp2 = [p1 + 0x4];',
                '8ab0100e:\t52 00       \tjump (p2);',
                '8ab01010:\t0c e1 6a 42 \tp4.l = 0x426a;\t\t/* (17002)\tp4=0x88a3426a */',
                '8ab01014:\t2f 2f       \tjump.s 0x8ab01000;',
                '8ab01016:\t64 00       \tcall (p4);',
                '8ab01018:\t20 32       \tp4 = r0;',
                '8ab0101a:\t64 00       \tcall (p4);',
            ]
        )
        self.assertEqual(call_targets(listing), {'88a3426a', 'indirect', 'unresolved 8ab01016', 'unresolved'})


if __name__ == '__main__':
    unittest.main()
