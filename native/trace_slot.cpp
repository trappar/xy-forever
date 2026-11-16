#include "trace_slot.hpp"
#include <stdint.h>

namespace xyf_core { namespace trace_slot {
namespace {
U32 word(const Byte* p) {
    return U32(p[0]) | U32(p[1]) << 8 | U32(p[2]) << 16 | U32(p[3]) << 24;
}
Result refused(Status status, Error error = Error::ok) {
    return {status, no_offset, error};
}
bool aliases(const Byte* a, U32 a_bytes, const Byte* b, U32 b_bytes) {
    const uintptr_t aa = reinterpret_cast<uintptr_t>(a);
    const uintptr_t bb = reinterpret_cast<uintptr_t>(b);
    // Subtraction avoids overflowing a pointer-sized integer at an endpoint.
    return aa <= bb ? bb - aa < a_bytes : aa - bb < b_bytes;
}
}
Result replace(Byte* image, U32 image_bytes, const Policy& policy, U32 target,
               const Byte* expected, const Byte* replacement, U32 slot_bytes) {
    if (!image || !expected || !replacement || !slot_bytes || slot_bytes > 4096 ||
        slot_bytes > 0xffffffffu - target)
        return refused(Status::invalid_argument);
    Summary summary;
    const Error validation = inspect(image, image_bytes, policy, summary);
    if (validation != Error::ok) return refused(Status::invalid_image, validation);
    if (aliases(image, image_bytes, replacement, slot_bytes))
        return refused(Status::replacement_alias);

    U32 slot = no_offset, matches = 0;
    bool overlap = false;
    const U32 end = target + slot_bytes;
    // inspect() has already checked every header and bound. Keep the same
    // stored-length rule, including FILL records with no stored payload.
    for (U32 offset = 0; offset < image_bytes;) {
        const U32 flags = word(image + offset) & 65535u;
        const U32 address = word(image + offset + 4);
        const U32 bytes = word(image + offset + 8);
        if (flags == 4 && address == target && bytes == slot_bytes) {
            ++matches;
            slot = offset + 16;
        } else if ((flags == 4 || flags == 0x104) && bytes &&
                   address < end && target < address + bytes) {
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
        if (image[slot + i] != expected[i]) return refused(Status::preimage_mismatch);
    for (U32 i = 0; i < slot_bytes; ++i) image[slot + i] = replacement[i];
    return {Status::ok, slot, Error::ok};
}
const char* status_name(Status status) {
    switch (status) {
    case Status::ok: return "ok";
    case Status::invalid_argument: return "invalid_argument";
    case Status::invalid_image: return "invalid_image";
    case Status::missing_slot: return "missing_slot";
    case Status::duplicate_slot: return "duplicate_slot";
    case Status::overlapping_record: return "overlapping_record";
    case Status::entry_overlap: return "entry_overlap";
    case Status::preimage_mismatch: return "preimage_mismatch";
    case Status::replacement_alias: return "replacement_alias";
    }
    return "unknown";
}
} }
