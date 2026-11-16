#ifndef XYF_RETAINED_LOOKUP_HPP
#define XYF_RETAINED_LOOKUP_HPP
#include <stdint.h>
namespace xy_forever { namespace retained_lookup {
typedef uint8_t Byte;
typedef uint32_t U32;
struct Range { U32 begin, end; };
struct Memory {
    void* user;
    bool (*read)(void*, U32 address, Byte* output, U32 count);
    const Range* readable; U32 readable_count;
    Range objects; // Only registered device allocations may live here.
};
enum class Result { ok, unavailable, argument, read, pointer, links, cycle,
                    capacity, name, duplicate, missing, heap, placement };
struct Diagnostic { U32 visited, name_bytes, reads, offending_address; };
static const U32 max_nodes = 64;
static const U32 name_capacity = 64;
static const U32 node_offset = 0xb4;
static const U32 device_bytes = 0xe94;
// Exact mount lookup for the retained stock list ABI: name pointer at device+0,
// next/prev at device+b4/b8. Trims leading/trailing '/', compares ASCII exactly.
// No allocator, stock function call or driver/MMIO. All reads are range checked;
// nodes, strings and cycles have explicit bounds. The complete list is checked
// before returning a unique result. On any failure, output device is zero.
Result find_exact(const Memory&, U32 sentinel, const char* name, U32& device, Diagnostic&);
// Exact1.2.1 retained-image ranges plus checked live heap extent. Necessary
// bounds are not evidence of valid live CPLBs or forward progress on hardware.
Result stock121_yaffs_device(U32& device);
// Same lookup, also returning its bounded traversal diagnostic.
Result stock121_yaffs_device(U32& device, Diagnostic& diagnostic);
} }
#endif
