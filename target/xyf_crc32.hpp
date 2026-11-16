#ifndef XYF_CRC32_HPP
#define XYF_CRC32_HPP
#include <stdint.h>
// zlib-compatible CRC-32: crc = update(0, data, n); chain by passing the
// previous result. About 8x fewer operations than the bitwise loop used in
// the test builds (relevant for 2-4 MB firmware images at every boot).
namespace xyf { namespace crc {
uint32_t update(uint32_t crc, const uint8_t* data, uint32_t count);
uint32_t update_volatile(uint32_t crc, const volatile uint8_t* data, uint32_t count);
} }
#endif
