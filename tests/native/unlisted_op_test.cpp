// target/unlisted_op.hpp: what diag.mtp (0x61) and diag.mtp_usb (0x62) answer to a request none of their ops takes:
// no op is an unlisted type, unsupported; an op the handler takes but malformed, bad_request (issue 113).
#include "doctest.h"
#include "unlisted_op.hpp"
namespace P = xyf::usbp;

TEST_CASE("unlisted op status") {
    const uint8_t ops[] = {0, 1, 2, 6, 7, 0x80, 0xfe, 0xff};
    for (uint8_t last = 0; last != 8; ++last) {
        CAPTURE(int(last));
        REQUIRE(xyf::unlisted_op::status(nullptr, 0, last) == P::s_unsupported);   // no op
        for (uint8_t op : ops)
            for (uint32_t length = 1; length != 4; ++length) {
                const uint8_t payload[3] = {op, 0, 0};
                const uint32_t want = op > last ? P::s_unsupported : P::s_bad_request;
                REQUIRE(xyf::unlisted_op::status(payload, length, last) == want);
            }
    }
}
