#ifndef XYF_COLD_HPP
#define XYF_COLD_HPP
#include <stdint.h>
namespace xy_forever { namespace cold {
static const uint32_t descriptor_magic = 0x584c4348u;
static const uint32_t source_base = 0x8b000000u;
static const uint32_t source_capacity = 0x800000u;
// CRCs identify already-loaded, untouched 1.2.1 RAM primitives. The builder
// derives them from the exact authenticated bootloader, never from a payload.
struct Descriptor {
    uint32_t magic, version;
    uint32_t payload_bytes, payload_crc32;
    uint32_t loader_crc32; // [0x88a346a6, 0x88a3481c)
    uint32_t cache_crc32;  // [0x8803d000, 0x8803d02a)
    uint32_t l1_copy_crc32; // [0x0803e0c0, 0x0803e142)
};
static_assert(sizeof(Descriptor) == 28, "cold descriptor layout");
} }
extern "C" const xy_forever::cold::Descriptor xy_forever_cold_descriptor
    __asm__("xy_forever_cold_descriptor");
extern "C" void xy_forever_cold_run() __asm__("xy_forever_cold_run");
#endif
