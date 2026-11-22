// The device encoder (target/usb_proto.cpp with the generated header) for tests/test_protocol_spec.py (ctypes;
// hosttest/CMakeLists.txt builds it).
#include "usb_proto.hpp"
extern "C" unsigned proto_encode(unsigned t, unsigned s, const uint8_t* p, unsigned n, uint8_t* out, unsigned cap) {
    return xyf::usbp::encode(uint8_t(t), uint8_t(s), p, n, out, cap);
}
