"""What the unlisted dev commands diag.mtp (0x61) and diag.mtp_usb (0x62) answer to a request none of their ops
takes (target/unlisted_op.hpp): no op is an unlisted type, unsupported (issue 113: the device's 0x62 answered
bad_request and failed hello/unlisted-types). The status itself: tests/native/unlisted_op_test.cpp."""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def handler(name):
    text = (ROOT / 'target' / name[0]).read_text()
    return re.search(
        r'\nvoid %s\(uint8_t seq, const uint8_t\* payload, U32 length\) \{(.*?)\n\}' % name[1], text, re.S
    ).group(1)


class UnlistedOpTest(unittest.TestCase):
    def test_both_handlers_answer_through_it(self):
        # Each handler's last op is the highest op it takes; the fallback passes exactly that.
        for name, call in (
            (('usb_service_mtp_session.ipp', 'mtp_tunnel'), 'code = xyf::unlisted_op::status(payload, length, %d);'),
            (
                ('usb_service_composite.ipp', 'mtp_usb_command'),
                'status_only(t_mtp_usb, seq, xyf::unlisted_op::status(payload, length, %d));',
            ),
        ):
            body = handler(name)
            last = max(int(n) for n in re.findall(r'\bop == (\d+) && length', body))
            self.assertIn(call % last, body, name[1])
            code = '\n'.join(line.split('//')[0] for line in body.splitlines())
            self.assertNotIn('s_unsupported', code, name[1])
        service = (ROOT / 'target/usb_service.cpp').read_text()
        self.assertIn('#if defined(XYF_MTP_SESSION) || defined(XYF_MTP_USB)\n#include "unlisted_op.hpp"', service)


if __name__ == '__main__':
    unittest.main()
