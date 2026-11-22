"""XY Forever storage formats (docs/STORAGE.md): host-built device C++."""

import unittest
from collections import namedtuple

import xyf_hosttest

State = namedtuple('State', 'default_id once stage2 stage2_previous stage2_tries')
Info = namedtuple('Info', 'name version kind bytes crc32 machines has_bytes has_crc32 has_machines has_generation')


class StoreTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lib = xyf_hosttest.module('store')

    def test_ids(self):
        for good in (b'opxy-121', b'a', b'x' * 31, b'open_xy-2'):
            self.assertTrue(self.lib.valid_id(good), good)
        for bad in (b'', b'x' * 32, b'Upper', b'a/b', b'..', b'a b', b'\xc3\xa9'):
            self.assertFalse(self.lib.valid_id(bad), bad)

    def state(self, text):
        s = self.lib.parse_state(text)
        self.assertIsNotNone(s)
        return State(*s)

    def test_state_parse_and_canonical_write(self):
        s = self.state(
            b'# XY Forever\r\ndefault = opxy-121 \r\nonce=open-xy-7\nunknown=1\n'
            b'stage2=xyf-0.2.ldr\nstage2_tries=2\ndefault=opxy-125\n'
        )
        self.assertEqual(
            (s.default_id, s.once, s.stage2, s.stage2_tries), (b'opxy-125', b'open-xy-7', b'xyf-0.2.ldr', 2)
        )
        out = self.lib.write_state(s, 256)
        self.assertEqual(out, b'default=opxy-125\nonce=open-xy-7\nstage2=xyf-0.2.ldr\nstage2_tries=2\n')
        again = self.state(out)
        self.assertEqual(again, s)
        self.assertEqual(self.lib.write_state(s, 10), b'')  # too small -> 0

    def test_state_rejects_bad_ids_and_oversize(self):
        s = self.state(b'default=../os\nonce=OK\nstage2_tries=99999999999\n')
        self.assertEqual((s.default_id, s.once, s.stage2_tries), (b'', b'', 0))
        big = b'#' * 4097
        self.assertIsNone(self.lib.parse_state(big))
        self.state(b'=x\nnoequals\n' + b'k' * 32 + b'=1\nv=' + b'x' * 128 + b'\n')  # all skipped, no crash

    def info(self, text):
        f = self.lib.parse_info(text)
        self.assertIsNotNone(f)
        return Info(*f)

    def test_info(self):
        f = self.info(b'name=OP-XY 1.2.5\nversion=1.2.5+0\nkind=official\nbytes=2303624\ncrc32=9f1c0a42\n')
        self.assertEqual(
            (f.name, f.version, f.kind, f.bytes, f.crc32, f.has_bytes, f.has_crc32),
            (b'OP-XY 1.2.5', b'1.2.5+0', 1, 2303624, 0x9F1C0A42, True, True),
        )
        f = self.info(b'kind=open-xy\ncrc32=9F1C0A42\nbytes=-1\n')
        self.assertEqual((f.kind, f.has_crc32, f.has_bytes), (2, False, False))

    def test_host_tool_output_round_trips(self):
        import zlib

        from xy_forever.xyf_entry import info_text

        data = bytes(range(256)) * 9
        f = self.info(info_text('OP-XY 1.2.5', '1.2.5+0', 'open-xy', data).encode())
        self.assertEqual(
            (f.name, f.version, f.kind, f.bytes, f.crc32, f.has_bytes, f.has_crc32),
            (b'OP-XY 1.2.5', b'1.2.5+0', 2, len(data), zlib.crc32(data), True, True),
        )


if __name__ == '__main__':
    unittest.main()
