#ifndef XYF_TFW_DEVICE_CHECK_HPP
#define XYF_TFW_DEVICE_CHECK_HPP
#include "../native/tfw_check.hpp"
namespace xy_forever { namespace tfw_device_check {
// Synchronous, non-reentrant; the caller owns file/context until this returns.
xyf_core::U32 run(const char* path, xyf_core::Byte* reply, const xyf_core::tfw_check::File& file);
} }
#endif
