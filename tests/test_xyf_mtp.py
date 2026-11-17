"""The file engine's run entry on a (historically "MTP mode", product build): the read-write
window and serve hook pins, stock calls, spans. A's decisions: tests/test_file_engine.py. No
device execution."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from xy_forever.source_text import unit_text  # noqa: E402
import xyf_build as builder
from xyf_build import (A_MARKER_CALLS, FORBIDDEN_TARGETS, MTP_CALLS, SERVER_CALLS, WRITE_ENTRIES, mtp_spans,
                       peer_spans, server_spans)
from xy_forever.paths import STOCK_121


class MtpTest(unittest.TestCase):
    """The engine's run entry (097's storage self-test; historically "MTP mode"): the read-write
    window, the serve hook, the stock calls and spans. The decisions: tests/test_file_engine.py."""

    def test_run_entry_pins(self):
        ss = (ROOT / 'target/storage_server.cpp').read_text()
        hpp = (ROOT / 'target/storage_server.hpp').read_text()
        self.assertIn('static const uint32_t engine_run_item = 6;', hpp)   # distinct from items 1-5 (menu, read, ...)
        latch = ss[ss.index('void latch(Stock121PeerSession& session) {'):]
        latch = latch[:latch.index('\n}\n')]
        self.assertIn('if (item == engine_run_item) entry = entry_run;', latch)
        # A failed stock code check refuses everything (refuse_off) but still follows b's restart.
        self.assertIn('engine_refused = r.identity_mismatch != 0;', latch)
        engine = ss[ss.index('void serve_engine('):]
        engine = engine[:engine.index('\n}\n')]
        self.assertIn('state.on = engine != entry_none && !engine_refused;', engine)
        self.assertIn('state.session = engine == entry_run ? window : mounted != 0;', engine)   # lazy write window

    def test_write_window_pins(self):
        src = (ROOT / 'target/cold_storage.cpp').read_text()
        end = src.index('Result Stock121ReadOnlySession::end() {\n    if (diagnostic_.handles)')
        repair_at = src.index('Result Stock121ReadOnlySession::repair_fresh(U32& step) {\n    step = 0;\n    if (!fresh_')
        body = src[src.index('Result Stock121ReadOnlySession::open_write_window('):repair_at]
        self.assertIn('if (!identity() || !direct_identity() || !write_identity()) return Result::identity;', body)
        self.assertEqual(body.count('y_mount(device_, 0)'), 1)         # one read-write mount
        self.assertNotIn('y_write(', body)                             # data writes only through the stock posix layer
        self.assertNotIn('y_open(', body)
        self.assertEqual(body.count('reinterpret_cast<U32>(&deny_media_change)'), 2)   # gates back on both exits
        # 192: the one other read-write mount, repair_fresh (yaffs repair after an unclean shutdown; the
        # counts include it). Only after a write_attempt failure, the same CRC
        # gates, no data writes, read-only again through mount_guarded() (the deny gates).
        repair = src[repair_at:end]
        self.assertIn('diagnostic_.result != Result::write_attempt', repair)
        self.assertIn('if (!identity() || !direct_identity() || !write_identity() || !capture_state(diagnostic_.before))', repair)
        self.assertEqual(repair.count('y_mount(device_, 0)'), 1)
        self.assertEqual(repair.count('y_unmount(device_)'), 1)
        self.assertNotIn('y_write(', repair)
        self.assertNotIn('y_open(', repair)
        self.assertEqual(repair.count('return mount_guarded();'), 1)
        users = sorted(p.name for p in (ROOT / 'target').glob('*.cpp') if 'repair_fresh(' in unit_text(p))
        self.assertEqual(users, ['cold_storage.cpp', 'file_boot.cpp'])
        guard = src.rindex('#if defined(XYF_MTP)', 0, src.index('Result Stock121ReadOnlySession::open_write_window('))
        self.assertGreater(guard, src.index('Result Stock121ReadOnlySession::write_state_slot('))
        users = sorted(p.name for p in (ROOT / 'target').glob('*.cpp') if 'open_write_window(' in unit_text(p))
        self.assertEqual(users, ['cold_storage.cpp', 'storage_server.cpp'])

    def test_serve_loop_pins(self):
        server = (ROOT / 'target/storage_server.cpp').read_text()
        # A writable engine entry takes precedence over install mode; the engine serves the rest.
        hook = server[server.index('void serve(void* context'):]
        self.assertLess(hook.index('latch(session);'), hook.index('#if defined(XYF_USB_SERVICE)'))
        self.assertLess(hook.index('engine != entry_write && engine != entry_run && serve_install('),
                        hook.index('serve_engine(request, vtable, op, storage, session);'))
        # The stock read-write provider mount and the write entries are never named.
        for address in WRITE_ENTRIES:
            self.assertNotIn('0x%08x' % address, server.lower())

    def test_calls_and_spans(self):
        self.assertFalse(MTP_CALLS & FORBIDDEN_TARGETS)
        self.assertFalse({'%08x' % a for a in WRITE_ENTRIES} & MTP_CALLS)
        self.assertNotIn('88a06a7e', MTP_CALLS)                          # never the boot-partition writer
        spans = mtp_spans()
        for address, length, _ in spans:
            self.assertFalse(0x11a00000 <= address < 0x11a0c000)
        covered = [(a, n) for a, n, _ in spans + server_spans() + peer_spans()]
        for call in MTP_CALLS | SERVER_CALLS | A_MARKER_CALLS:
            self.assertTrue(any(a <= int(call, 16) < a + n for a, n in covered), call)

    def test_spans_match_bootloader(self):
        boot = STOCK_121 / 'bootloader.ldr'
        if not boot.exists():
            self.skipTest('private bootloader absent')
        from xy_forever.build_formats import Firmware
        firmware = Firmware(boot.read_bytes())
        for address, length, crc in mtp_spans():
            self.assertEqual(zlib.crc32(firmware.read(address, length)), crc, hex(address))

    def test_only_the_product_has_mtp(self):
        self.assertNotIn('xyf-mtp', builder.MODES)                       # removed
        self.assertEqual([m for m in builder.MODES if builder.features(m)['mtp']], ['xyf-product'])
        src = (ROOT / 'xyf_build.py').read_text()
        self.assertIn("require(not has('serve_only')", src)

    def test_ranged_read_lseek_is_the_file_client_s(self):
        # b's ranged read seeks with the partition builder (op 0x2c), never stock lseek 889f297c.
        self.assertIn('889f297c', builder.REMOVED_CLIENT_CALLS)
        for name in ('storage_server.cpp', 'usb_service.cpp', 'file_client.cpp'):
            self.assertNotIn('0x889f297cu', unit_text(ROOT / 'target' / name), name)
        client = (ROOT / 'target/file_client.cpp').read_text()
        self.assertIn('Reply seek(int fd, int offset) { return partition(0x2c, U32(fd), U32(offset), 0); }', client)

    def test_host_stubs_compile(self):
        with tempfile.TemporaryDirectory(prefix='xyf-mtp-syntax-') as tmp:
            for name in ('cold_storage', 'peer_services', 'storage_server', 'file_engine', 'file_client'):
                subprocess.run(['/usr/bin/clang++', '-std=c++11', '-fsyntax-only', '-Wall', '-Werror',
                                '-ffreestanding', '-fno-builtin', '-fno-exceptions', '-fno-rtti', '-DXYF_MTP=1',
                                '-DXYF_USB_FILE_WRITE=1',
                                '-I', str(ROOT / 'target'), '-I', str(ROOT / 'native'),
                                str(ROOT / 'target' / (name + '.cpp'))], check=True, capture_output=True, cwd=tmp)


if __name__ == '__main__':
    unittest.main()
