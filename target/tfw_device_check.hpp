#ifndef XYF_TFW_DEVICE_CHECK_HPP
#define XYF_TFW_DEVICE_CHECK_HPP
#include "../native/tfw_check.hpp"
namespace xy_forever { namespace tfw_device_check {
// Synchronous, non-reentrant; the caller owns file/context until this returns.
xyf_core::U32 run(const char* path, xyf_core::Byte* reply, const xyf_core::tfw_check::File& file);
// A's hash must cover this same immutable source. The authenticated archive borrows
// the reader and Work until its consumer calls close(). Only the .tfw key index 0 is admitted.
struct Authentication {
    const xyf_core::input::Reader& source;
    const xyf_core::hash::Digest& ciphertext;
    xyf_core::tfw::Archive& archive;
    xyf_core::Byte* header;  // tfw::header_bytes, caller-owned
    xyf_core::Work* work;
};
xyf_core::tfw::Status authenticate(const char* path, const Authentication& request);
} }
#endif
