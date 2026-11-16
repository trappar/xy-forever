"""Stage-2 "hello" path (--xyf-stage2): b's decisions (same C++ as the device),
the result record and decoder, read-only and PLAY-only rules, buffers, and the
builder's single-trampoline audit. No device execution."""
import ctypes as C
from pathlib import Path
import re
import struct
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from xy_forever import hostbuild  # noqa: E402
import xyf_build as builder
from xy_forever import xyf_stage2 as S
from xy_forever.census import (FILE_BOOT_BYTES, FILE_BOOT_FIELDS, FILE_BOOT_LIBRARY_BYTES, FILE_BOOT_MAGIC,
                                   FILE_BOOT_MENU_BYTES, FILE_BOOT_STAGE2_BYTES, FILE_BOOT_STAGE2_FIELDS,
                                   STAGE2_CHECKS, STAGE2_STATUS, file_boot)

XYF = (ROOT / 'target/xyf_boot.hpp').read_text()
STAGE2_H = (ROOT / 'target/xyf_stage2.hpp').read_text()
FILE_BOOT = (ROOT / 'target/file_boot.cpp').read_text()
FILE_BOOT_H = (ROOT / 'target/file_boot.hpp').read_text()
KEY_UI = (ROOT / 'target/key_ui.cpp').read_text()
HELLO = (ROOT / 'target/stage2_hello.cpp').read_text()
ENTER = (ROOT / 'target/xyf_stage2_enter.s').read_text()
READY, REFUSED, INVALID = range(3)
MENU, BOOT_DEFAULT, BOOT_BUILTIN = range(3)
BEGIN = 0x8ab90000


def body(text, start):
    begin = text.index(start)
    return text[begin:text.index('\n}\n', begin)]


def constant(text, name):
    return int(re.search(r'\b' + name + r' = (0x[0-9a-f]+|\d+)u?[;,]', text).group(1), 0)


class Stage2DecisionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='xyf-stage2-boot-')
        lib = Path(cls.tmp.name) / 'stage2.dylib'
        hostbuild.run(['/usr/bin/clang++', '-std=c++11', '-O1', '-shared', '-fPIC', '-Wall', '-Wextra', '-Werror',
                       '-ffreestanding', '-fno-builtin', str(ROOT / 'target/xyf_stage2.cpp'),
                       str(ROOT / 'target/xyf_crc32.cpp'), str(ROOT / 'native/hash.cpp'),
                       str(ROOT / 'tests/xyf_stage2_bridge.cpp'), '-o', str(lib)], check=True, capture_output=True)
        cls.lib = C.CDLL(str(lib))
        cls.lib.stage2_classify.argtypes = [C.c_char_p, C.c_uint, C.c_uint, C.POINTER(C.c_uint)]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def classify(self, data, stage1=1):
        detail = C.c_uint(0xdead)
        return self.lib.stage2_classify(data, len(data), stage1, C.byref(detail)), detail.value

    def test_blob_decisions(self):
        good = S.pack(b'\x01\x02' * 64, BEGIN, BEGIN, 1, 'hello')
        self.assertEqual(self.classify(good), (READY, 0))
        note = struct.pack('<II', 0x4e535958, 3 | 0 << 8)               # a: open failed (no file)
        self.assertEqual(self.classify(note), (REFUSED, 3))
        self.assertEqual(self.classify(struct.pack('<II', 0x4e535958, 5 | 11 << 8)), (REFUSED, 5 | 11 << 8))
        self.assertEqual(self.classify(b'XYSN' + bytes(5))[0], INVALID)     # 9 bytes: not a note
        self.assertEqual(self.classify(b''), (INVALID, STAGE2_CHECKS.index('size')))
        self.assertEqual(self.classify(good[:-1]), (INVALID, STAGE2_CHECKS.index('size')))
        self.assertEqual(self.classify(good, stage1=0), (INVALID, STAGE2_CHECKS.index('stage1')))
        bad = bytearray(good); bad[-1] ^= 0xff
        self.assertEqual(self.classify(bytes(bad)), (INVALID, STAGE2_CHECKS.index('image_crc')))
        self.assertEqual(constant(STAGE2_H, 'refusal_magic'), struct.unpack('<I', b'XYSN')[0])

    def test_exit_decisions(self):
        self.assertEqual(self.lib.stage2_after(0, 0), BOOT_DEFAULT)
        self.assertEqual(self.lib.stage2_after(1, 0), MENU)
        self.assertEqual(self.lib.stage2_after(7, 0), BOOT_DEFAULT)          # unknown: as continue
        for code in (0, 1, 7):
            self.assertEqual(self.lib.stage2_after(code, 1), BOOT_BUILTIN)


class Stage2BootTest(unittest.TestCase):
    def test_record_layout_matches_decoder(self):
        block = XYF[XYF.index('struct Stage2Info {'):XYF.index('};', XYF.index('struct Stage2Info {'))]
        names = [n.strip() for decl in re.findall(r'uint32_t ([^;]+);', block) for n in decl.split(',')]
        self.assertEqual(names, FILE_BOOT_STAGE2_FIELDS)
        self.assertEqual(FILE_BOOT_STAGE2_BYTES, 4 * len(names))
        statuses = re.search(r'enum Stage2Status : uint32_t \{([^}]+)\}', XYF).group(1)
        self.assertEqual([n.split('=')[0].strip()[3:] for n in statuses.split(',')], STAGE2_STATUS)
        checks = re.search(r'enum class Check : uint32_t \{([^}]+)\}', STAGE2_H).group(1)
        self.assertEqual([n.strip() for n in checks.split(',')], STAGE2_CHECKS)
        result = FILE_BOOT_H[FILE_BOOT_H.index('struct Result {'):]
        self.assertLess(result.index('xyf_boot::MenuInfo menu;'), result.index('xyf_boot::Stage2Info stage2;'))
        self.assertIn('r.version = 4;', FILE_BOOT)

    def test_decoder_reads_stage2_block(self):
        menu_end = FILE_BOOT_BYTES + FILE_BOOT_LIBRARY_BYTES + FILE_BOOT_MENU_BYTES
        words = [0] * ((menu_end + FILE_BOOT_STAGE2_BYTES) // 4)
        for name, value in dict(magic=FILE_BOOT_MAGIC, version=4, role=1, outcome=1, committed=1).items():
            words[FILE_BOOT_FIELDS.index(name)] = value
        at = menu_end // 4
        words[at:at + 8] = [1, 0, 1719, 1, BEGIN, 0, 900, 4200]
        block = struct.pack('<%dI' % len(words), *words)
        out = file_boot([dict(address=0x8ab06000, bytes=len(block), offset=0)], block)
        self.assertEqual(out['stage2']['status'], 'ready')
        self.assertEqual((out['stage2']['check'], out['stage2']['entry'], out['stage2']['exit']),
                         ('ok', hex(BEGIN), 'continue'))
        words[at:at + 8] = [8, 3, 8, 0, 0, 0xffffffff, 0, 0]               # b: a refused (no file)
        block = struct.pack('<%dI' % len(words), *words)
        out = file_boot([dict(address=0x8ab06000, bytes=len(block), offset=0)], block)['stage2']
        self.assertEqual((out['status'], out['a_status'], out['check'], out['exit']), ('refused', 'open', 'ok', None))
        self.assertIn('menu', file_boot([dict(address=0, bytes=len(block), offset=0)], block))

    def test_read_only_and_play_only(self):
        serve = body(FILE_BOOT, 'void a_serve_stage2(')
        self.assertIn('read_image(storage, xyf::stage2::path, bytes)', serve)
        self.assertNotRegex(serve, r'write|unlink|mkdir|rename|install|06a7e')
        load = body(FILE_BOOT, 'void b_load_stage2(')
        self.assertNotRegex(load, r'write_state|unlink|storage\.')
        # Copy verified before the cache work; the entry is published last.
        self.assertLess(load.index('crc32(to, header.image_bytes)'), load.index('prepare_code('))
        self.assertGreater(load.index('b_stage2_entry = header.entry'), load.index('prepare_code('))
        # b asks for stage 2 only inside the PLAY branch, after the catalog.
        role1 = body(FILE_BOOT, 'bool role1_menu_boot(')
        role1 = role1[role1.index('#else\n    if (!b_lost && r.menu.play) {'):]   # Test 059: legacy branch (stage 2 builds)
        play = role1.index('if (!b_lost && r.menu.play) {')
        self.assertGreater(role1.index('b_load_stage2(session)'), play)
        self.assertLess(role1.index('b_load_stage2(session)'), role1.index('xy_forever_key_ui();'))
        self.assertEqual(role1.count('b_load_stage2('), 1)
        # key_ui (PLAY only) runs it once, through the asm trampoline.
        self.assertEqual(KEY_UI.count('bool run_stage2()'), 2)              # hello + ABI-2 variants (#if)
        self.assertEqual(KEY_UI.count('if (run_stage2())'), 1)              # one call
        self.assertEqual(KEY_UI.count('xyf_stage2_enter(entry, &abi)'), 1)
        self.assertNotIn('stage2', FILE_BOOT[FILE_BOOT.index('extern "C" void xy_forever_file_boot()'):]
                         .replace('r.stage2.exit = 0xffffffffu;', ''))

    def test_items_buffers_and_window(self):
        items = {name: constant(XYF, name) for name in ('item_catalog', 'item_boot_default', 'item_boot_entry',
                                                       'item_stage2')}
        self.assertEqual(items['item_stage2'], 0x12)
        self.assertFalse(items['item_boot_entry'] <= items['item_stage2'] < items['item_boot_entry'] + 16)
        self.assertNotIn(items['item_stage2'], (items['item_catalog'], items['item_boot_default']))
        self.assertFalse(0x40 <= items['item_stage2'] < 0x65)                   # set-default / USB id items
        begin, end = constant(STAGE2_H, 'window_begin'), constant(STAGE2_H, 'window_end')
        self.assertEqual((begin, end), builder.STAGE2_WINDOW)
        self.assertEqual((begin, end), S.WINDOW)
        for lo, hi in ((0x8a000000, 0x8a800000), (0x8ab00000, builder.WIDE_STACK_END), (0x8b000000, 0x8b800000),
                       (0x8b800000, 0x8b810000), (0x8b810000, 0x8b823000), (0x8b820000, 0x8c000000)):
            self.assertTrue(end <= lo or begin >= hi, (hex(lo), hex(hi)))
        self.assertIn('stage2_ms = 15000', XYF)

    def test_hello_image_rules(self):
        self.assertIn('section(".text.entry")', HELLO)
        self.assertNotRegex(HELLO, r'0x8[89a][0-9a-f]{5}|0x11[0-9a-f]{6}|0x20[0-9a-f]{6}')   # no stock addresses
        self.assertIn('return stage2::exit_menu;', HELLO)                  # unknown stage 1
        self.assertIn('event == stage2::event_link_lost', HELLO)
        self.assertEqual(re.findall(r'(?:CALL|JUMP) \(P\d\)', ENTER), ['JUMP (P0)'])

    def test_audit_accepts_only_the_trampoline(self):
        listing = '\n'.join([
            '8ab04790 <_other>:',
            '8ab04790:\t00 32       \tp0 = r0;',
            '8ab04792:\t50 00       \tjump (p0);',
            '8ab047b0 <xyf_stage2_enter>:',
            '8ab047b0:\t00 32       \tp0 = r0;',
            '8ab047b2:\t01 30       \tr0 = r1;',
            '8ab047b4:\t50 00       \tjump (p0);',
        ])
        self.assertEqual(builder.call_targets(listing), {'unresolved', 'stage2-enter'})
        self.assertEqual(builder.stage2_enter_sites(listing), 1)
        self.assertEqual(builder.stage2_enter_sites(listing + '\n8ab047b6:\t50 00       \tjump (p0);'), 2)
        self.assertEqual(builder.MODES['xyf-stage2'], ('xyf-stage2-032', 'stage2-032-#.bin'))
        self.assertEqual(builder.FILE_VARIANT_REPORTS['xyf-stage2'], 'file-032-#.bin')
        self.assertNotIn('xyf-stage2', builder.WRITE_AUDIT_MODES)          # no write of any kind


if __name__ == '__main__':
    unittest.main()
