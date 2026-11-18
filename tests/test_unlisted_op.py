"""What the unlisted dev commands diag.mtp (0x61) and diag.mtp_usb (0x62) answer to a request none of their ops
takes (target/unlisted_op.hpp): no op is an unlisted type, unsupported (issue 113: the device's 0x62 answered
bad_request and failed hello/unlisted-types)."""
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from xy_forever import hostbuild  # noqa: E402

PROGRAM = r'''
#include <stdio.h>
#include "unlisted_op.hpp"
namespace P = xyf::usbp;
int main() {
    unsigned checks = 0;
    const uint8_t ops[] = {0, 1, 2, 6, 7, 0x80, 0xfe, 0xff};
    for (uint8_t last = 0; last != 8; ++last) {
        if (xyf::unlisted_op::status(nullptr, 0, last) != P::s_unsupported) return 1;   // no op
        for (uint8_t op : ops)
            for (uint32_t length = 1; length != 4; ++length) {
                const uint8_t payload[3] = {op, 0, 0};
                const uint32_t want = op > last ? P::s_unsupported : P::s_bad_request;
                if (xyf::unlisted_op::status(payload, length, last) != want) return 2;
                ++checks;
            }
    }
    printf("%u checks passed\n", checks);
    return 0;
}
'''


def handler(name):
    text = (ROOT / 'target' / name[0]).read_text()
    return re.search(r'\nvoid %s\(uint8_t seq, const uint8_t\* payload, U32 length\) \{(.*?)\n\}' % name[1],
                     text, re.S).group(1)


class UnlistedOpTest(unittest.TestCase):
    def test_status_on_host(self):
        with tempfile.TemporaryDirectory(prefix='xyf-unlisted-') as tmp:
            source, binary = Path(tmp) / 'unlisted_op_test.cpp', Path(tmp) / 'unlisted_op'
            source.write_text(PROGRAM)
            hostbuild.run(['/usr/bin/clang++', '-std=c++11', '-Wall', '-Wextra', '-Werror',
                           '-fsanitize=address,undefined', '-I', str(ROOT / 'target'), str(source),
                           '-o', str(binary)], check=True, capture_output=True, text=True)
            result = subprocess.run([str(binary)], check=True, capture_output=True, text=True)
            self.assertRegex(result.stdout, r'[1-9][0-9]* checks passed')

    def test_both_handlers_answer_through_it(self):
        # Each handler's last op is the highest op it takes; the fallback passes exactly that.
        for name, call in ((('usb_service_mtp_session.ipp', 'mtp_tunnel'), 'code = xyf::unlisted_op::status(payload, length, %d);'),
                           (('usb_service_composite.ipp', 'mtp_usb_command'),
                            'status_only(t_mtp_usb, seq, xyf::unlisted_op::status(payload, length, %d));')):
            body = handler(name)
            last = max(int(n) for n in re.findall(r'\bop == (\d+) && length', body))
            self.assertIn(call % last, body, name[1])
            code = '\n'.join(line.split('//')[0] for line in body.splitlines())
            self.assertNotIn('s_unsupported', code, name[1])
        service = (ROOT / 'target/usb_service.cpp').read_text()
        self.assertIn('#if defined(XYF_MTP_SESSION) || defined(XYF_MTP_USB)\n#include "unlisted_op.hpp"', service)


if __name__ == '__main__':
    unittest.main()
