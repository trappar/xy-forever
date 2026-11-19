#include "tfw_device_check.hpp"
#if defined(__bfin__) && defined(XYF_TFW_CHECK)
#include "otp_keys.hpp"
#include "usb_proto.hpp"
namespace {
using xyf_core::U32;
using xyf_core::Byte;
using namespace xy_forever;
// Separate address spaces on A/B. Existing command/request exclusion makes this invocation
// synchronous. Only run() sets this pointer to its caller's live automatic File, then clears it.
const xyf_core::tfw_check::File* active_file __attribute__((section(".data"))) = 0;
__attribute__((noinline, noclone)) U32 tfw_check_run(const char* path, Byte* reply) {
    (void)path;
    xyf_core::tfw_check::Workspace work;
    xyf_core::tfw_check::Result result;
    const xyf_core::tfw_check::Keys keys = {0, otp_keys::public_key, {0, otp_keys::aes_key}};
    const xyf_core::tfw_check::Reason reason = xyf_core::tfw_check::check(*active_file, keys, work, result);
    xyf::usbp::put32(reply, reason == xyf_core::tfw_check::Reason::pass ? 0u : 1u);
    xyf::usbp::put32(reply + 4, U32(reason));
    for (U32 i = 0; i != 32; ++i) reply[8 + i] = result.public_key_read ? result.public_key_sha256.bytes[i] : 0;
    return xyf::usbp::s_ok;
}
}
namespace xy_forever { namespace tfw_device_check {
U32 run(const char* path, Byte* reply, const xyf_core::tfw_check::File& file) {
    if (active_file) return xyf::usbp::s_busy;
    active_file = &file;
    const U32 code = tfw_check_run(path, reply);
    active_file = 0;
    return code;
}
} }
#endif
