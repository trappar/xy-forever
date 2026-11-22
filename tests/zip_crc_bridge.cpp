// The ZIP CRC (native/inflate.cpp) beside tests/xyf_crc32_bridge.cpp in the XYF_SMALL_CRC build (crc32_small,
// hosttest/CMakeLists.txt) for tests/test_xyf_crc32.py.
#include "../native/inflate.hpp"
extern "C" unsigned zip_crc(const unsigned char* d, unsigned n) {
    return xyf_core::zip::crc32_update(0xffffffffu, d, n) ^ 0xffffffffu;
}
