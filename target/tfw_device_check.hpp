#ifndef XYF_TFW_DEVICE_CHECK_HPP
#define XYF_TFW_DEVICE_CHECK_HPP
#include "../native/tfw_check.hpp"
namespace xy_forever { namespace tfw_device_check {
// Synchronous, non-reentrant; the caller owns file/context until this returns.
xyf_core::U32 run(const char* path, xyf_core::Byte* reply, const xyf_core::tfw_check::File& file);
// The sound pack import's archive (#279): Archive::open_unsigned with the device's AES key (index 0 only). It
// borrows the reader and Work until its consumer calls close().
struct Opening {
    const xyf_core::input::Reader& source;
    xyf_core::tfw::Archive& archive;
    xyf_core::Byte* header;  // tfw::header_bytes, caller-owned
    xyf_core::Work* work;
};
xyf_core::tfw::Status open(const char* path, const Opening& request);
}}
#endif
