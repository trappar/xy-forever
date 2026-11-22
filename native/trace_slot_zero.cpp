#include "trace_slot.hpp"
// Large zero-filled diagnostic slot lookup, kept separate from replace() so
// images that never use it link byte-identically to hardware-proven builds.
namespace xyf_core { namespace trace_slot {
namespace {
U32 word(const Byte* p) { return U32(p[0]) | U32(p[1]) << 8 | U32(p[2]) << 16 | U32(p[3]) << 24; }
Result refused(Status status, Error error = Error::ok) { return {status, no_offset, error}; }
}
Result locate_zero(const Byte* image, U32 image_bytes, const Policy& policy, U32 target, U32 slot_bytes) {
    if (!image || !slot_bytes || slot_bytes > max_zero_slot || slot_bytes > 0xffffffffu - target)
        return refused(Status::invalid_argument);
    Summary summary;
    const Error validation = inspect(image, image_bytes, policy, summary);
    if (validation != Error::ok) return refused(Status::invalid_image, validation);
    U32 slot = no_offset, matches = 0;
    bool overlap = false;
    const U32 end = target + slot_bytes;
    // Same stored-length rule as replace(), after inspect() checked all bounds.
    for (U32 offset = 0; offset < image_bytes;) {
        const U32 flags = word(image + offset) & 65535u;
        const U32 address = word(image + offset + 4);
        const U32 bytes = word(image + offset + 8);
        if (flags == 4 && address == target && bytes == slot_bytes) {
            ++matches;
            slot = offset + 16;
        } else if ((flags == 4 || flags == 0x104) && bytes && address < end && target < address + bytes) {
            overlap = true;
        }
        offset += 16 + (flags == 0x104 ? 0 : bytes);
    }
    if (!matches) return refused(Status::missing_slot);
    if (matches != 1) return refused(Status::duplicate_slot);
    if (overlap) return refused(Status::overlapping_record);
    if ((summary.entry >= target && summary.entry - target < slot_bytes) ||
        (summary.entry + 1 >= target && summary.entry + 1 - target < slot_bytes))
        return refused(Status::entry_overlap);
    for (U32 i = 0; i < slot_bytes; ++i)
        if (image[slot + i]) return refused(Status::preimage_mismatch);
    return {Status::ok, slot, Error::ok};
}
}}
