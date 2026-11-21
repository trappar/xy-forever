#ifndef XYF_CRC32_SMALL_HPP
#define XYF_CRC32_SMALL_HPP
#include <stdint.h>
namespace xyf_core { namespace crc {
// Shared narrow-layout IEEE CRC state, before final inversion. Volatile byte input
// guarantees one source load per byte; callers choose running/finalized convention.
__attribute__((noinline)) inline uint32_t small_update(uint32_t running, const volatile uint8_t* data, uint32_t count) {
    static const uint32_t table[16] = {
        0x00000000u,0x1db71064u,0x3b6e20c8u,0x26d930acu,
        0x76dc4190u,0x6b6b51f4u,0x4db26158u,0x5005713cu,
        0xedb88320u,0xf00f9344u,0xd6d6a3e8u,0xcb61b38cu,
        0x9b64c2b0u,0x86d3d2d4u,0xa00ae278u,0xbdbdf21cu
    };
    for (uint32_t i = 0; i != count; ++i) {
        running ^= data[i];
        running = (running >> 4) ^ table[running & 15u];
        running = (running >> 4) ^ table[running & 15u];
    }
    return running;
}
} }
#endif
