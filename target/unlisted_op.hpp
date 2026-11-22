#ifndef XYF_UNLISTED_OP_HPP
#define XYF_UNLISTED_OP_HPP
// The status a test-only dev command that protocol/v1.json does not list (diag.mtp 0x61, diag.mtp_usb 0x62)
// answers to a request none of its ops takes (host test: tests/test_unlisted_op.py). No op, or an op above
// its last one, answers as an unlisted type (unsupported: conformance case hello/unlisted-types); a known op
// with a wrong length is bad_request.
#include "usb_proto_ids.hpp"
namespace xyf { namespace unlisted_op {
inline uint32_t status(const uint8_t* payload, uint32_t length, uint8_t last_op) {
    return !length || payload[0] > last_op ? usbp::s_unsupported : usbp::s_bad_request;
}
}}
#endif
