"""Stage 2 main (ABI 2, --xyf-stage2-menu): the real stage-2 menu program run
on the host against a scripted stage 1, stage 1's request decisions (same C++
as the device), the 32-bit ABI layout (cross compiler), and the builder/source
rules. No device execution."""

import ctypes as C
import re
import shutil
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

import pytest
import xyf_hosttest
from conftest import blackfin

from xy_forever import xyf_build as builder
from xy_forever import xyf_stage2 as S
from xy_forever.source_text import unit_text

ROOT = Path(__file__).resolve().parents[1]

KEY_UI = unit_text(ROOT / 'target/key_ui.cpp')
STAGE2_H = (ROOT / 'target/xyf_stage2.hpp').read_text()
MENU = (ROOT / 'target/stage2_menu.cpp').read_text()
FILE_BOOT = unit_text(ROOT / 'target/file_boot.cpp')
MINUS, PLUS, PLAY, STOP, SHIFT = 53, 54, 51, 52, 55
NEVER = 0xFFFFFFFF
BOOT_DEFAULT, BOOT_ENTRY, MENU_FALLBACK, BOOT_BUILTIN, SET_DEFAULT, MTP, NOTE_UNAVAILABLE, NOTE_NO_ENTRY = range(8)
F_CATALOG, F_SET_DEFAULT, F_MTP, F_UNINSTALL = 1, 2, 4, 8


def press(key):
    return [key | 1 << 8, key]


def catalog_blob(entries, default=0xFFFFFFFF):
    """XYC1 blob exactly as processor a encodes it (target/xyf_catalog.cpp)."""
    body = struct.pack('<4I', 0x31435958, len(entries), default, 0)
    for ident, name, kind, flags in entries:
        body += (
            ident.encode().ljust(32, b'\0')
            + name.encode().ljust(48, b'\0')
            + b'1.0'.ljust(32, b'\0')
            + struct.pack('<BBH', kind, flags, 0)
        )
    return body + struct.pack('<I', zlib.crc32(body))


LIBRARY = catalog_blob(
    [('opxy-121', 'OP-XY 1.2.1', 1, 1), ('granular', 'open-xy: granular', 2, 7), ('opxy-125', 'OP-XY 1.2.5', 1, 1)],
    default=0,
)


def build_stage2(tmp, tag):
    # Its own copy of the library (hosttest/CMakeLists.txt): a fresh load, so fresh static state.
    lib = Path(tmp) / ('stage2-menu-%s.so' % tag)
    shutil.copy2(xyf_hosttest.program('stage2_menu'), lib)
    lib = C.CDLL(str(lib))
    lib.s2_run.argtypes = [
        C.POINTER(C.c_uint16),
        C.POINTER(C.c_uint),
        C.c_uint,
        C.c_uint,
        C.c_char_p,
        C.c_uint,
        C.c_uint,
        C.c_char_p,
        C.c_uint,
        C.POINTER(C.c_uint),
        C.POINTER(C.c_uint),
    ]
    lib.s2_run.restype = C.c_uint
    lib.s2_constant.restype = C.c_uint
    return lib


class Stage2Program:
    """One loaded stage 2 (its .data persists across runs, as on the device)."""

    def __init__(self, lib):
        self.lib = lib
        self.fb = (C.c_uint16 * (480 * 256))()

    def run(self, keys=(), lost=NEVER, blob=LIBRARY, features=F_CATALOG, note=None, abi=2):
        arr = (C.c_uint * max(1, len(keys)))(*keys)
        frames, ms = C.c_uint(0), C.c_uint(0)
        exit = self.lib.s2_run(
            self.fb,
            arr,
            len(keys),
            lost,
            blob,
            len(blob) if blob else 0,
            features,
            note.encode() if note else None,
            abi,
            C.byref(frames),
            C.byref(ms),
        )
        return exit, frames.value, ms.value


@pytest.mark.gate
class Stage2MenuProgramTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='xyf-stage2-menu-')
        cls.count = 0

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def fresh(self):
        type(self).count += 1
        return Stage2Program(build_stage2(self.tmp.name, self.count))

    def test_constants_match_host_and_python(self):
        lib = self.fresh().lib
        values = [lib.s2_constant(i) for i in range(13)]
        self.assertEqual(values, [S.ABI_HELLO, S.ABI_MENU, 0, 1, 2, 3, 4, 5, 1, 2, 4, 8, 32])

    def test_unknown_stage1_gets_menu_without_drawing(self):
        p = self.fresh()
        self.assertEqual(p.run(abi=1)[:2], (1, 0))  # exit_menu: stage 1 keeps its own menu
        self.assertEqual(p.run(abi=3)[:2], (1, 0))

    def test_continue_and_mods_requests(self):
        p = self.fresh()
        exit, frames, _ = p.run(press(PLAY))  # main row 0: Continue
        self.assertEqual(exit, 0)
        self.assertGreaterEqual(frames, 1)
        # The safe-mode row (no Firmware library page, no boot once or set default from the menu): no saved setup
        # here, PLAY (SHIFT too) only shows why; nothing is requested until the idle Continue.
        exit, frames, ms = self.fresh().run(press(PLUS) + press(PLAY) + [SHIFT | 1 << 8] + press(PLAY))
        self.assertEqual(exit, 0)
        self.assertGreaterEqual(frames, 2)
        self.assertGreater(ms, 60000)
        # #197: no Uninstall row (four rows: Settings, then Mods above it); nothing here requests an uninstall.
        self.assertEqual(self.fresh().run(press(MINUS) * 2 + press(PLAY))[0], 1)  # Mods, below Settings's wrap
        p = self.fresh()  # Mods -> the stage-1 menu runs the page
        self.assertEqual(p.run(press(PLUS) * 2 + press(PLAY))[0], 1)  # right below the safe-mode row

    def test_link_loss_and_idle_return_continue(self):
        p = self.fresh()
        self.assertEqual(p.run(press(PLUS), lost=1)[0], 0)
        exit, frames, ms = self.fresh().run()
        self.assertEqual((exit, frames), (0, 1))
        self.assertGreater(ms, 60000)
        self.assertLess(ms, 60100)

    def test_invalid_or_withheld_catalog_keeps_the_rows(self):
        bad = bytearray(LIBRARY)
        bad[-1] ^= 1
        keys = press(PLUS) + press(PLAY) + press(PLAY) + press(PLUS) + press(PLAY)
        for blob, features in ((bytes(bad), F_CATALOG), (LIBRARY, 0), (None, F_CATALOG), (LIBRARY[:-1], F_CATALOG)):
            # The safe-mode row's PLAY requests nothing, PLUS moves to Mods: the stage-1 menu (exit 1).
            self.assertEqual(
                self.fresh().run(keys, blob=blob, features=features | F_UNINSTALL)[0], 1, (features, blob and len(blob))
            )

    def test_reentry_keeps_place_and_shows_note(self):
        p = self.fresh()
        keys = press(MINUS) * 2 + [SHIFT | 1 << 8] + press(PLAY)
        self.assertEqual(p.run(keys, features=F_CATALOG | F_UNINSTALL)[0], 1)  # Mods (SHIFT left held)
        # Called again with a note: main page, menu row kept, so MINUS, PLUS, PLAY asks again.
        exit, frames, _ = p.run(
            press(MINUS) + press(PLUS) + press(PLAY),
            note='Not available in this build.',
            features=F_CATALOG | F_UNINSTALL,
        )
        self.assertEqual(exit, 1)
        self.assertGreaterEqual(frames, 2)

    def test_draws_only_through_services_and_never_writes_outside_the_frame(self):
        p = self.fresh()
        canary = (C.c_uint16 * (480 * 256 + 64))()
        for i in range(len(canary)):
            canary[i] = 0xA5A5
        frames, ms = C.c_uint(0), C.c_uint(0)
        arr = (C.c_uint * 2)(*press(PLAY))
        p.lib.s2_run(
            C.cast(C.byref(canary, 32 * 2), C.POINTER(C.c_uint16)),
            arr,
            2,
            NEVER,
            LIBRARY,
            len(LIBRARY),
            F_CATALOG,
            None,
            2,
            C.byref(frames),
            C.byref(ms),
        )
        self.assertTrue(all(canary[i] == 0xA5A5 for i in range(32)))
        self.assertTrue(all(canary[i] == 0xA5A5 for i in range(32 + 480 * 256, len(canary))))


class Stage1DecisionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lib = xyf_hosttest.module('stage2')

    def decide(self, exit, lost=0, count=3, features=F_CATALOG):
        return self.lib.decide(exit, lost, count, features)

    def test_requests(self):
        self.assertEqual(self.decide(0), (BOOT_DEFAULT, 0))
        self.assertEqual(self.decide(1), (MENU_FALLBACK, 0))
        self.assertEqual(self.decide(2 | 2 << 8), (BOOT_ENTRY, 2))
        self.assertEqual(self.decide(2 | 3 << 8), (NOTE_NO_ENTRY, 0))  # out of range
        self.assertEqual(self.decide(2 | 0 << 8, features=0), (NOTE_NO_ENTRY, 0))  # no catalog
        self.assertEqual(self.decide(2 | 1 << 8, count=0), (NOTE_NO_ENTRY, 0))
        # Set default only where the build offers it (write builds), and only in range.
        self.assertEqual(self.decide(3 | 1 << 8), (NOTE_UNAVAILABLE, 0))
        self.assertEqual(self.decide(3 | 1 << 8, features=F_CATALOG | F_SET_DEFAULT), (SET_DEFAULT, 1))
        self.assertEqual(self.decide(3 | 5 << 8, features=F_CATALOG | F_SET_DEFAULT), (NOTE_NO_ENTRY, 0))
        self.assertEqual(self.decide(4), (NOTE_UNAVAILABLE, 0))
        self.assertEqual(self.decide(4, features=F_MTP), (MTP, 0))
        self.assertEqual(self.decide(5), (NOTE_UNAVAILABLE, 0))
        self.assertEqual(self.decide(5, features=F_UNINSTALL), (NOTE_UNAVAILABLE, 0))  # no uninstall yet
        for unknown in (6, 0xFF, 0xFFFFFFFF, 0x1234500):  # as ABI 1: continue
            self.assertEqual(self.decide(unknown)[0], BOOT_DEFAULT)
        for code in (0, 1, 2 | 1 << 8, 3, 4, 5, 9):  # link lost wins
            self.assertEqual(self.decide(code, lost=1), (BOOT_BUILTIN, 0))


class Stage2MenuRulesTest(unittest.TestCase):
    @pytest.mark.gate
    @pytest.mark.toolchain
    def test_device_layout_32_bit(self):
        flags = ['-mcpu=bf707', '-std=gnu++11', '-ffreestanding', '-fsyntax-only', '-I', ROOT / 'target']
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'menu.cpp').write_text(
                '#include <stddef.h>\n#include "xyf_stage2.hpp"\nusing namespace xyf::stage2;\n'
                'static_assert(sizeof(Abi) == 32 && sizeof(Abi2) == 48, "size");\n'
                'static_assert(offsetof(Abi2, v1) == 0 && offsetof(Abi2, catalog) == 32 && '
                'offsetof(Abi2, catalog_bytes) == 36 && offsetof(Abi2, features) == 40 && '
                'offsetof(Abi2, note) == 44, "offsets");\n'
                'static_assert(offsetof(Abi, services) == 28 && offsetof(Services, now_ms) == 16, "v1");\n'
                'static_assert(abi == 2 && this_stage1 == 2, "menu stage 1");\n'
            )
            (Path(tmp) / 'hello.cpp').write_text(
                '#include "xyf_stage2.hpp"\nstatic_assert(xyf::stage2::abi == 1 && '
                'xyf::stage2::this_stage1 == 1, "hello stage 1");\n'
            )
            blackfin(self, [['g++', *flags, '-DXYF_STAGE2_MENU=1', 'menu.cpp'], ['g++', *flags, 'hello.cpp']], tmp)

    def test_stage1_runner_rules(self):
        menu = KEY_UI[KEY_UI.index('#if defined(XYF_STAGE2_MENU)\n// ABI 2') : KEY_UI.index('#else\n// Runs a loaded')]
        self.assertEqual(menu.count('xyf_stage2_enter('), 1)  # one call site per variant
        self.assertIn('xyf_stage2_enter(entry, &abi2.v1)', menu)
        self.assertEqual(KEY_UI.count('xyf_stage2_enter(entry, &abi)'), 1)  # hello variant unchanged
        self.assertIn('round != max_rounds', menu)
        self.assertIn('abi2.v1.bytes = sizeof(abi2)', menu)
        # Writes only through the existing set_default path, only in write builds.
        write = menu.index('#if defined(XYF_STATE_WRITE)')
        self.assertLess(write, menu.index('feature_set_default'))
        self.assertLess(write, menu.index('xyf_boot::set_default(chosen)'))
        self.assertNotRegex(menu, r'write_state|unlink|install|06a7e')
        self.assertIn('const uint8_t* catalog_blob(uint32_t& bytes)', FILE_BOOT)

    def test_stage2_program_rules(self):
        self.assertIn('section(".text.entry")', MENU)
        self.assertNotRegex(MENU, r'0x8[89a][0-9a-f]{5}|0x11[0-9a-f]{6}|0x20[0-9a-f]{6}')  # no stock addresses
        self.assertIn('return stage2::exit_menu;', MENU)  # unknown stage 1
        self.assertIn('event == stage2::event_link_lost', MENU)
        self.assertNotRegex(MENU, r'reinterpret_cast<[^>]*\*>\(0x')  # no absolute pointers

    def test_report_decoder_names_requests(self):
        from xy_forever.census import STAGE2_REQUESTS, stage2_exit_name

        block = re.search(r'enum Request : uint32_t \{([^}]+)\}', STAGE2_H).group(1)
        names = re.findall(r'request_(\w+) = (\d+)', block)
        self.assertEqual([n for n, _ in names], STAGE2_REQUESTS)
        self.assertEqual([int(v) for _, v in names], list(range(len(STAGE2_REQUESTS))))
        self.assertEqual(
            [stage2_exit_name(x) for x in (0, 1, 2 | 1 << 8, 3 | 2 << 8, 4, 5, 9, 0xFFFFFFFF)],
            ['continue', 'menu', 'boot_once 1', 'set_default 2', 'mtp', 'uninstall', 9, None],
        )

    def test_builder_mode(self):
        self.assertEqual(builder.MODES['xyf-stage2-menu'], 'stage2m-#.bin')
        self.assertEqual(builder.FILE_VARIANT_REPORTS['xyf-stage2-menu'], 'file2m-#.bin')
        self.assertNotIn('xyf-stage2-menu', builder.WRITE_AUDIT_MODES)  # no write of any kind
        self.assertIn('target/stage2_menu.cpp', builder.own_sources())
        image = b'\x01\x02' * 64
        for abi in (S.ABI_HELLO, S.ABI_MENU):
            self.assertEqual(S.inspect(S.pack(image, S.WINDOW[0], S.WINDOW[0], 1, 'x', abi=abi))['abi'], abi)
        with self.assertRaises(ValueError):
            S.pack(image, S.WINDOW[0], S.WINDOW[0], 1, 'x', abi=4)


if __name__ == '__main__':
    unittest.main()
