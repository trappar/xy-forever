"""XY Forever storage formats (docs/STORAGE.md): host-built device C++."""
import ctypes as C
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from xy_forever import hostbuild  # noqa: E402


class State(C.Structure):
    _fields_ = [('default_id', C.c_char * 32), ('once', C.c_char * 32), ('stage2', C.c_char * 64),
                ('stage2_previous', C.c_char * 64), ('stage2_tries', C.c_uint32)]


class Info(C.Structure):
    _fields_ = [('name', C.c_char * 48), ('version', C.c_char * 32), ('kind', C.c_uint32),
                ('bytes', C.c_uint32), ('crc32', C.c_uint32), ('machines', C.c_uint32), ('has_bytes', C.c_bool), ('has_crc32', C.c_bool), ('has_machines', C.c_bool), ('has_generation', C.c_bool)]


class StoreTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='xyf-store-')
        lib = Path(cls.tmp.name) / 'store.dylib'
        hostbuild.run(['/usr/bin/clang++', '-std=c++11', '-O1', '-shared', '-fPIC', '-Wall', '-Wextra', '-Werror',
                       '-ffreestanding', '-fno-builtin', '-fno-exceptions', '-fno-rtti',
                       str(ROOT / 'target/xyf_store.cpp'), str(ROOT / 'target/xyf_info.cpp'), str(ROOT / 'tests/xyf_store_bridge.cpp'),
                       '-o', str(lib)], check=True, capture_output=True)
        cls.lib = C.CDLL(str(lib))
        cls.lib.store_sizes.restype = C.c_uint

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_struct_layouts_match(self):
        self.assertEqual([self.lib.store_sizes(i) for i in range(2)],
                         [C.sizeof(State), C.sizeof(Info)])

    def test_ids(self):
        for good in (b'opxy-121', b'a', b'x' * 31, b'open_xy-2'):
            self.assertTrue(self.lib.store_valid_id(good), good)
        for bad in (b'', b'x' * 32, b'Upper', b'a/b', b'..', b'a b', b'\xc3\xa9'):
            self.assertFalse(self.lib.store_valid_id(bad), bad)

    def state(self, text):
        s = State()
        self.assertEqual(self.lib.store_state(text, len(text), C.byref(s)), 1)
        return s

    def test_state_parse_and_canonical_write(self):
        s = self.state(b'# XY Forever\r\ndefault = opxy-121 \r\nonce=open-xy-7\nunknown=1\n'
                       b'stage2=xyf-0.2.ldr\nstage2_tries=2\ndefault=opxy-125\n')
        self.assertEqual((s.default_id, s.once, s.stage2, s.stage2_tries),
                         (b'opxy-125', b'open-xy-7', b'xyf-0.2.ldr', 2))
        out = C.create_string_buffer(256)
        n = self.lib.store_write_state(C.byref(s), out, 256)
        self.assertEqual(out.raw[:n], b'default=opxy-125\nonce=open-xy-7\nstage2=xyf-0.2.ldr\nstage2_tries=2\n')
        again = self.state(out.raw[:n])
        self.assertEqual(bytes(again), bytes(s))
        self.assertEqual(self.lib.store_write_state(C.byref(s), out, 10), 0)   # too small -> 0

    def test_state_rejects_bad_ids_and_oversize(self):
        s = self.state(b'default=../os\nonce=OK\nstage2_tries=99999999999\n')
        self.assertEqual((s.default_id, s.once, s.stage2_tries), (b'', b'', 0))
        big = b'#' * 4097
        self.assertEqual(self.lib.store_state(big, len(big), C.byref(State())), 0)
        self.state(b'=x\nnoequals\n' + b'k' * 32 + b'=1\nv=' + b'x' * 128 + b'\n')   # all skipped, no crash

    def test_info(self):
        f = Info()
        text = b'name=OP-XY 1.2.5\nversion=1.2.5+0\nkind=official\nbytes=2303624\ncrc32=9f1c0a42\n'
        self.assertEqual(self.lib.store_info(text, len(text), C.byref(f)), 1)
        self.assertEqual((f.name, f.version, f.kind, f.bytes, f.crc32, f.has_bytes, f.has_crc32),
                         (b'OP-XY 1.2.5', b'1.2.5+0', 1, 2303624, 0x9f1c0a42, True, True))
        text = b'kind=open-xy\ncrc32=9F1C0A42\nbytes=-1\n'
        self.assertEqual(self.lib.store_info(text, len(text), C.byref(f)), 1)
        self.assertEqual((f.kind, f.has_crc32, f.has_bytes), (2, False, False))

    def test_host_tool_output_round_trips(self):
        import sys, zlib
        sys.path.insert(0, str(ROOT))
        from xy_forever.xyf_entry import info_text
        data = bytes(range(256)) * 9
        text = info_text('OP-XY 1.2.5', '1.2.5+0', 'open-xy', data).encode()
        f = Info()
        self.assertEqual(self.lib.store_info(text, len(text), C.byref(f)), 1)
        self.assertEqual((f.name, f.version, f.kind, f.bytes, f.crc32, f.has_bytes, f.has_crc32),
                         (b'OP-XY 1.2.5', b'1.2.5+0', 2, len(data), zlib.crc32(data), True, True))


if __name__ == '__main__':
    unittest.main()
