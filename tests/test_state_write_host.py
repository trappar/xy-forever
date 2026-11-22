"""Host side of 'set default': slot parser vs device writer, dry run, protocol constants."""

import io
import re
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import xyf_hosttest

from xy_forever import usb_proto as P
from xy_forever import xyf_host as H

ROOT = Path(__file__).resolve().parents[1]


class StateWriteHostTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lib = xyf_hosttest.module('state')

    def slot(self, entry_id, seq):
        return self.lib.slot_default(entry_id.encode(), seq, 512)

    def test_host_parser_matches_device_writer(self):
        a, b = self.slot('opxy-121', 3), self.slot('granular', 4)
        self.assertEqual(
            H.parse_slot(a),
            (
                3,
                {
                    'default': 'opxy-121',
                    'seq': '3',
                },
            ),
        )
        self.assertEqual(H.current_state({'a': a, 'b': b})[1:], (4, {'default': 'granular', 'seq': '4'}))
        self.assertEqual(H.current_state({'a': a, 'b': b[:-3]})[0], 'a')  # torn b: a wins
        self.assertIsNone(H.current_state({'a': None, 'b': None}))
        for i in range(len(a)):
            broken = bytearray(a)
            broken[i] ^= 1
            self.assertIsNone(H.parse_slot(bytes(broken)), i)

    def test_dry_run_and_refusals(self):
        for argv, expected in (
            (['set-default', 'opxy-121'], 0),
            (['set-default', 'Bad Id'], 2),
            (['set-default', '../x'], 2),
        ):
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(H.main(argv), expected)
            if expected == 0:
                self.assertIn('DRY RUN', out.getvalue())

    def test_census_decodes_state_write_record(self):
        from xy_forever import census, census_usb

        word = 1 | 0 << 8 | 10 << 16 | 2 << 24  # ok, storage ok, step 10, seq 2
        self.assertEqual(census.state_write_summary(word), dict(status='ok', storage='ok', step=10, seq=2))
        word = 6 | census.STORAGE_RESULTS.index('verify_failed') << 8 | 7 << 16
        self.assertEqual(census.state_write_summary(word)['storage'], 'verify_failed')
        self.assertIsNone(census.state_write_summary(0))
        self.assertEqual(census_usb.USB_SERVICE_FIELDS[-1], 'a_state_write')
        self.assertEqual(census.FILE_BOOT_MENU_FIELDS[-1], 'state_write')
        # cold_storage::Result order matches the decoder (new values appended).
        hpp = (ROOT / 'target/cold_storage.hpp').read_text()
        body = hpp[hpp.index('enum class Result {') + 19 : hpp.index('};', hpp.index('enum class Result {'))]
        names = [n.strip() for n in re.sub(r'//.*', '', body).replace('\n', ' ').split(',') if n.strip()]
        self.assertEqual(names, census.STORAGE_RESULTS)

    def test_protocol_constants_match_device(self):
        header = (ROOT / 'target/usb_proto_ids.hpp').read_text()
        self.assertEqual(int(re.search(r't_set_default = (0x[0-9a-f]+)', header).group(1), 16), P.SET_DEFAULT)
        sw = (ROOT / 'target/xyf_state_write.hpp').read_text()
        self.assertIn('static const uint32_t item_set_default = 0x40;', sw)
        self.assertIn('static const uint32_t item_id_nibble = 0x68;', sw)  # nibbles, low bits 0
        self.assertEqual(H.SLOTS, ('/yaffs2/xy-forever/state-a.txt', '/yaffs2/xy-forever/state-b.txt'))


if __name__ == '__main__':
    unittest.main()
