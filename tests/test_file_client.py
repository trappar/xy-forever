"""B's timed file client (file_client.cpp): the six admitted stock calls, their
scope and CRC spans, the request bounds, the removed stock client. Behaviour (silent, slow, lossy
A): tests/test_simulator.py. No device execution."""
import ctypes as C
from pathlib import Path
import struct
import tempfile
import unittest
import zlib

from xy_forever.source_text import unit_text
from xy_forever import hostbuild
from xy_forever import xyf_build as B
from xy_forever import usb_proto_ids as ids
from xy_forever.paths import STOCK_121

ROOT = Path(__file__).resolve().parents[1]

APPROVED_B = {'889f8d22', '889f6c5c', '889f8c10', '889f8e36', '889f88ac'}   # changes need the developer's explicit yes
APPROVED_A = {'889fa834'}
APPROVED_A_FIND = {'889fa2be'}                 # #6: the final-link guard's lookup, that one caller only


class AllowlistTest(unittest.TestCase):
    def test_exactly_the_approved_calls(self):
        self.assertEqual(B.FILE_CLIENT_CALLS, APPROVED_B)
        self.assertEqual(B.A_RENAME_CALLS, APPROVED_A)
        self.assertEqual((B.A_FIND_CALLS, B.A_FIND_CALLERS), (APPROVED_A_FIND, ('24final_link_reaches_inbox',)))
        self.assertFalse(APPROVED_A_FIND & B.FORBIDDEN_TARGETS)
        self.assertFalse((APPROVED_A | APPROVED_B) & B.FORBIDDEN_TARGETS)
        self.assertLessEqual({'889f869e', '889f7b12'}, B.FORBIDDEN_TARGETS)   # the unbounded stock RPC

    def test_the_stock_client_is_gone_from_b(self):
        self.assertEqual(len(B.REMOVED_CLIENT_CALLS), 12)
        for name in ('CLIENT_CALLS', 'MTP_TEST_CALLS', 'FILE_WRITE_CALLS'):
            self.assertFalse(hasattr(B, name), name)
        # Only a's marker check keeps three of them (open, close, stat), scoped to a's functions.
        allowed = B.KEY_UI_CALLS | B.PEER_CALLS | B.STORAGE_CALLS | B.SERVER_CALLS | B.A_MARKER_CALLS | \
            B.A_RENAME_CALLS | B.USB_CALLS | B.FILE_CLIENT_CALLS | B.MTP_CALLS
        self.assertEqual(allowed & B.REMOVED_CLIENT_CALLS, B.A_MARKER_CALLS)
        self.assertEqual(B.A_MARKER_CALLS, {'889f3516', '889f3b50', '889f285a'})
        for name in ('usb_service.cpp', 'file_client.cpp', 'file_boot.cpp', 'menu_link.cpp'):
            text = unit_text(ROOT / 'target' / name)
            for call in B.REMOVED_CLIENT_CALLS:
                self.assertNotIn('0x%su' % call, text, (name, call))

    def test_scopes(self):
        src = (ROOT / 'xy_forever/xyf_build.py').read_text()
        for line in ("check_scoped_calls(raw, FILE_CLIENT_CALLS, '11file_client', ('11file_client',), 'File client')",
                     "check_scoped_calls(raw, A_RENAME_CALLS, '14storage_server6a_side', ('14storage_server',), 'Rename')",
                     "check_scoped_calls(raw, A_FIND_CALLS, '14storage_server6a_side', A_FIND_CALLERS, 'Final-link lookup')",
                     "check_scoped_calls(raw, A_MARKER_CALLS - SERVER_CALLS, '14storage_server6a_side', A_MARKER_CALLERS,"):
            self.assertIn(line, src)
        server = ''.join((ROOT / 'target' / n).read_text() for n in ('storage_server.cpp', 'storage_server_engine.ipp',
                                                                      'storage_server_marker.ipp'))
        for name in ('bool stat_into(', 'void restore_marker(', 'void close_handles('):
            self.assertIn('__attribute__((noinline)) ' + name, server)
        client = unit_text(ROOT / 'target/file_client.cpp')
        for call in APPROVED_B:
            self.assertEqual(client.count('stock<0x%su' % call), 1, call)       # one call site each
        self.assertNotIn('guard_pause', client)

    def test_path_copied_only_after_the_slot_is_claimed(self):
        # a request left out past its bound points at path_buffer, and the stock driver reads
        # it only when it sends: no caller may write it before claim() succeeded.
        client = unit_text(ROOT / 'target/file_client.cpp')
        for name in ('Reply path_request(', 'Reply query('):
            body = client[client.index(name):]
            body = body[:body.index('\n}\n')]
            self.assertIn('place(path);', body, name)
            self.assertLess(body.index('claim(session, why)'), body.index('place(path);'), name)

    def test_spans(self):
        client = B.client_spans()
        self.assertTrue(set(map(tuple, client)) <= set(map(tuple, B.peer_spans())))
        for call in APPROVED_B:
            self.assertTrue(any(a <= int(call, 16) < a + n for a, n, _ in client), call)
        for call in APPROVED_A:
            self.assertTrue(any(a <= int(call, 16) < a + n for a, n, _ in B.server_spans()), call)
        boot = STOCK_121 / 'bootloader.ldr'
        if not boot.exists():
            self.skipTest('private bootloader absent')
        from xy_forever.build_formats import Firmware
        firmware = Firmware(boot.read_bytes())
        for address, length, crc in client:
            self.assertEqual(zlib.crc32(firmware.read(address, length)), crc, hex(address))


class BoundsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='xyf-file-client-')
        lib = Path(cls.tmp.name) / 'client.dylib'
        hostbuild.run(['/usr/bin/clang++', '-std=c++11', '-O1', '-shared', '-fPIC', '-Wall', '-Wextra', '-Werror',
                       '-ffreestanding', '-fno-builtin', '-fno-exceptions', '-fno-rtti', '-I', str(ROOT / 'target'),
                       str(ROOT / 'target/file_client.cpp'), str(ROOT / 'tests/file_client_bridge.cpp'),
                       '-o', str(lib)], check=True, capture_output=True)
        cls.lib = C.CDLL(str(lib))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_bounds(self):
        short, long = self.lib.fc_request_ms(), self.lib.fc_change_ms()
        self.assertEqual((short, long), (5000, 45000))         # 009: 5 s; 45 s (a read-write mount took 16.85 s)
        for op, argument in ((8, 0), (9, 0), (0x25, 0), (0x26, 0), (0x27, 0), (0x2b, 0), (0x2d, 3), (0x29, 0x8301)):
            self.assertEqual(self.lib.fc_bound_ms(op, argument), long, hex(op))
        for op, argument in ((0x29, 0), (0x2a, 0), (0x2c, 3), (0x2e, 0), (0x2f, 0), (0x30, 0), (0x22, 0), (0x23, 0)):
            self.assertEqual(self.lib.fc_bound_ms(op, argument), short, hex(op))
        # file.rename's rename_to opens the write window and renames (long), as rename_over and rename_upload (#144);
        # rename_from only keeps it.
        for argument in (0x4f544e52, 0x4f545052, 0x54555052):
            self.assertEqual(self.lib.fc_bound_ms(0x21, argument), long, hex(argument))
        for argument in (0x52464e52, 0, 4):
            self.assertEqual(self.lib.fc_bound_ms(0x21, argument), short, hex(argument))
        boot = (ROOT / 'target/xyf_boot.hpp').read_text()
        self.assertIn('static const uint32_t lost_ms = 60000;', boot)   # > the longest bound and a's 25 s alone reset

    def test_status(self):
        self.assertEqual(self.lib.fc_status(1), ids.S_REFUSED)        # refused: never sent
        for outcome in (2, 3, 4):                                     # link, busy, lost
            self.assertEqual(self.lib.fc_status(outcome), ids.S_LINK)

    def test_change_status(self):
        # A's yaffs error numbers (1.2.1 listing 889fa4b0): ENOENT 2, A's policy EACCES 13, ENOTEMPTY 93.
        for error, status in ((2, ids.S_NOT_FOUND), (13, ids.S_REFUSED), (93, ids.S_STATE), (17, ids.S_STORAGE),
                              (20, ids.S_STORAGE), (21, ids.S_STORAGE), (30, ids.S_STORAGE), (0, ids.S_STORAGE)):
            self.assertEqual(self.lib.fc_change_status(error), status, error)
        # file.rename: A's EEXIST (the target exists: refused before the stock rename) is state.
        for error, status in ((17, ids.S_STATE), (2, ids.S_NOT_FOUND), (13, ids.S_REFUSED), (93, ids.S_STATE),
                              (22, ids.S_STORAGE), (5, ids.S_STORAGE), (0, ids.S_STORAGE)):
            self.assertEqual(self.lib.fc_rename_status(error), status, error)

    def entry(self, name, kind, have=None, room=300):
        dirent = bytearray(0x10c)
        dirent[10] = kind
        dirent[11:11 + len(name)] = name
        record = bytearray(0x34)
        if have:
            record[8:12] = struct.pack('<I', have[0])
            record[0x1c:0x20] = struct.pack('<I', have[1])
        out = C.create_string_buffer(300)
        n = self.lib.fc_list_entry(bytes(dirent), 1 if have else 0, bytes(record), out, room)
        return out.raw[:n]

    def test_list_entry(self):
        name = 'caf\u00e9.txt'.encode('utf-8')
        self.assertEqual(self.entry(name, 8, (0x81a4, 1234)), b'\x00' + struct.pack('<I', 1234) + b'\x09' + name)
        self.assertEqual(self.entry(b'sub', 8, (0x41ed, 2048)), b'\x01' + bytes(4) + b'\x03sub')   # stat decides
        self.assertEqual(self.entry(b'sub', 4), b'\x01' + bytes(4) + b'\x03sub')                  # no stat: d_type
        self.assertEqual(self.entry(b'link', 10, (0xa1ff, 9))[0], 2)
        self.assertEqual(self.entry(b'link', 10, (0x41ed, 0))[0], 2)   # a link to a folder: never a folder
        self.assertEqual(self.entry(b'f', 8)[:5], b'\x00' + bytes(4))                             # no stat: no size
        self.assertEqual(len(self.entry(b'x' * 300, 8)), 6 + 255)                                   # names stop at 255
        self.assertEqual(self.entry(b'abc', 8, room=8), b'')                                         # 9 B do not fit
        self.assertEqual(len(self.entry(b'abc', 8, room=9)), 9)

    def test_readdir_result(self):
        # stock 889f6c02 answers A's dirent address (build 165 took only 1 as an entry: every
        # device folder listed empty), 0 at the end; A's refusal -1 (storage_server).
        entry, end, error = 0, 1, 2
        for value in (0x8806f9a4 - (1 << 32), 0x0804f700, 1, 0x7fffffff, -2):
            self.assertEqual(self.lib.fc_readdir_result(value), entry, hex(value))
        self.assertEqual(self.lib.fc_readdir_result(0), end)
        self.assertEqual(self.lib.fc_readdir_result(-1), error)
        # All three B file-client walks use it: file.list, rename depth, and native metadata name lookup.
        client = unit_text(ROOT / 'target/file_client.cpp')
        self.assertEqual(client.count(' = readdir('), 3)
        self.assertEqual(client.count('readdir_result(r.value)'), client.count(' = readdir('))
        self.assertIn('C::readdir_result(int32_t(v))', unit_text(ROOT / 'target/usb_service.cpp'))

    def test_child_path(self):
        out = C.create_string_buffer(240)
        for folder, name, want in ((b'/yaffs2', b'a', b'/yaffs2/a'), (b'/yaffs2/', b'a', b'/yaffs2/a'),
                                   (b'/yaffs2/x', b'b c', b'/yaffs2/x/b c')):
            self.assertEqual(self.lib.fc_child_path(folder, name, out), 1)
            self.assertEqual(out.value, want)
        self.assertEqual(self.lib.fc_child_path(b'/yaffs2/' + b'd' * 220, b'n' * 10, out), 1)    # 239 B + NUL
        self.assertEqual(self.lib.fc_child_path(b'/yaffs2/' + b'd' * 220, b'n' * 11, out), 0)


if __name__ == '__main__':
    unittest.main()
