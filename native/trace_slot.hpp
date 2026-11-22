#ifndef XYF_TRACE_SLOT_HPP
#define XYF_TRACE_SLOT_HPP
#include "ldr.hpp"

namespace xyf_core { namespace trace_slot {
enum class Status {
    ok,
    invalid_argument,
    invalid_image,
    missing_slot,
    duplicate_slot,
    overlapping_record,
    entry_overlap,
    preimage_mismatch,
    replacement_alias
};
static const U32 no_offset = 0xffffffffu;
struct Result {
    Status status;
    U32 file_offset; // Stored DATA bytes on success; no_offset on failure.
    Error validation_error; // inspect() error only for invalid_image.
};
// Experimental diagnostic edit, never a RAM loader. Caller exclusively owns
// image throughout this operation and supplies readable expected/replacement
// buffers of slot_bytes bytes. Replacement must not overlap image. The entire
// stream and exact preimage are checked before any write. Headers, size and all
// bytes outside the unique slot remain unchanged. No allocation or cache/MMIO.
Result replace(Byte* image, U32 image_bytes, const Policy& policy, U32 target, const Byte* expected,
               const Byte* replacement, U32 slot_bytes);
// Same whole-stream checks for a large zero-filled diagnostic slot (up to
// max_zero_slot bytes) without copying: preimage must be all zero. Returns the
// stored offset; the caller then fills it. image is not modified.
static const U32 max_zero_slot = 0x400000u;
Result locate_zero(const Byte* image, U32 image_bytes, const Policy& policy, U32 target, U32 slot_bytes);
const char* status_name(Status status);
}}
#endif
