#ifndef XYF_BOOT_ROM_HPP
#define XYF_BOOT_ROM_HPP

#include <stdint.h>
#include <stddef.h>

// OP-XY bootloader 1000.0.20+0: observed ROM data layout, not a complete
// runnable handoff. See docs/hardware/usb.md, Boot partition and USB (boot ROM).
// This module deliberately contains no ROM call or persistent-write primitive.
namespace xy_forever { namespace boot_rom {

static const uint32_t source_address = 0x8a000000u;
static const uint32_t rom_boot_address = 0x04000008u;
static const uint32_t result_entry_address = 0x20000020u;
static const size_t context_size = 600;
static const size_t auxiliary_size = 16;
static const size_t configuration_offset = 0xb8;
static const size_t reader_offset = configuration_offset + 8;
static const size_t auxiliary_pointer_offset = 0x7c;

struct Context {
    alignas(4) uint8_t bytes[context_size];
};
struct Auxiliary {
    alignas(4) uint8_t bytes[auxiliary_size];
};

inline void put32(uint8_t* p, uint32_t value) {
    for (unsigned i = 0; i != 4; ++i) p[i] = static_cast<uint8_t>(value >> (8 * i));
}

// Addresses are explicit target addresses rather than truncated host pointers.
// Context and auxiliary storage must be retained outside every payload write.
inline bool prepare(Context& context, Auxiliary& auxiliary, uint32_t reader_target, uint32_t auxiliary_target) {
    if (!reader_target || (reader_target & 1u) || !auxiliary_target || (auxiliary_target & 3u)) return false;
    for (size_t i = 0; i != context_size; ++i) context.bytes[i] = 0;
    for (size_t i = 0; i != auxiliary_size; ++i) auxiliary.bytes[i] = 0;
    put32(context.bytes + reader_offset, reader_target);
    put32(context.bytes + auxiliary_pointer_offset, auxiliary_target);
    return true;
}

// The stock callback reads offset, destination and length from three consecutive
// 32-bit words. It advances offset only after copying. The ROM owns other fields.
struct ReadRequest {
    uint32_t offset;
    uint32_t destination;
    uint32_t count;
};
static_assert(sizeof(ReadRequest) == 12, "ROM read request prefix");

enum class ReadResult { ok, source_bounds, destination_bounds, copy_failed };
typedef bool (*Copy)(void* user, uint32_t destination, const uint8_t* source, uint32_t count);

// Pure callback model. The actual native callback must stop boot irrevocably on
// failure: ROM's observed stock callback has no established error-return channel.
// Copy supplies target-memory validation and cache maintenance; it must not be a
// bare memcpy into an arbitrary address supplied by a malformed stream.
inline ReadResult read(ReadRequest& request, const uint8_t* source, uint32_t source_size, Copy copy, void* user) {
    if (request.offset > source_size || request.count > source_size - request.offset) return ReadResult::source_bounds;
    if (!request.count) return ReadResult::ok;
    if (!source || !copy || request.count - 1u > UINT32_MAX - request.destination)
        return ReadResult::destination_bounds;
    if (!copy(user, request.destination, source + request.offset, request.count)) return ReadResult::copy_failed;
    request.offset += request.count;
    return ReadResult::ok;
}

}}
#endif
