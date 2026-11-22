"""Cancellation through the signed TFW reader, ZIP members and OS writer."""
import random
import unittest

from test_ldr import image
from test_tfw_os import OS, VERSION, GOOD_VERSION, PUBLIC_KEY, member, package, signed

import xyf_hosttest


class TfwWorkTests(unittest.TestCase):
    def test_cancellation_discards_results_and_stops_io(self):
        code = b'\x01\x02'
        payload = bytes(range(256)) * 400
        loader = image([(4, 0x11a00000, len(code), 0, code), (4, 0x80001000, len(payload), 0, payload)],
                       entry=0x11a00000)
        content = random.Random(4301).randbytes(7000)
        rows = [member(VERSION, GOOD_VERSION), member(OS, loader),
                member('firmware/blackfin/content/example', content)]
        xyf_hosttest.module('tfw_work').run(signed(package(rows)), PUBLIC_KEY)   # raises on a failed check
