#include "tfw_device_check.hpp"
#if defined(__bfin__) && defined(XYF_TFW_CHECK)
#include "otp_keys.hpp"
#include "usb_proto.hpp"
#include "../native/tfw_signature.hpp"
namespace {
using xyf_core::Byte;
using xyf_core::U32;
using namespace xy_forever;
// Separate address spaces on A/B. Existing command/request exclusion makes this invocation
// synchronous. Only run() sets this pointer to its caller's live automatic File, then clears it.
const xyf_core::tfw_check::File* active_file __attribute__((section(".data"))) = 0;
const tfw_device_check::Authentication* active_auth __attribute__((section(".data"))) = 0;
__attribute__((noinline)) bool verify_vendor(void* context, const Byte* header, U32 size) {
    using namespace xyf_core;
    if (size != tfw::crypto_header_bytes || header[4] || header[tfw::signature_key_index]) return false;
    const auto& keys = *static_cast<const tfw_check::Keys*>(context);
    Byte public_key[tfw::signature_public_key_bytes];
    const bool got = keys.public_key(keys.context, public_key);
    const bool ok = got && tfw::verify_header(header, size, public_key) == tfw::Signature::valid;
    volatile Byte* wipe = public_key;
    for (U32 i = 0; i != sizeof(public_key); ++i) wipe[i] = 0;
    return ok;
}
__attribute__((noinline, noclone)) U32 tfw_check_run(const char* path, Byte* reply) {
    (void)path;
    xyf_core::tfw_check::Keys keys = {0, otp_keys::public_key, {0, otp_keys::aes_key}};
    if (active_auth) {
        const auto& r = *active_auth;
        const xyf_core::tfw::HeaderCheck signature = {&keys, verify_vendor};
        return U32(r.archive.open_digested(r.source, keys.aes, r.ciphertext, signature, r.header,
                                           xyf_core::tfw::op_xy_sku, r.work));
    }
    xyf_core::tfw_check::Workspace work;
    xyf_core::tfw_check::Result result;
    const xyf_core::tfw_check::Reason reason = xyf_core::tfw_check::check(*active_file, keys, work, result);
    xyf::usbp::put32(reply, reason == xyf_core::tfw_check::Reason::pass ? 0u : 1u);
    xyf::usbp::put32(reply + 4, U32(reason));
    for (U32 i = 0; i != 32; ++i) reply[8 + i] = result.public_key_read ? result.public_key_sha256.bytes[i] : 0;
    return xyf::usbp::s_ok;
}
}
namespace xy_forever { namespace tfw_device_check {
U32 run(const char* path, Byte* reply, const xyf_core::tfw_check::File& file) {
    if (active_file || active_auth) return xyf::usbp::s_busy;
    active_file = &file;
    const U32 code = tfw_check_run(path, reply);
    active_file = 0;
    return code;
}
xyf_core::tfw::Status authenticate(const char* path, const Authentication& request) {
    using xyf_core::tfw::Status;
    if (active_file || active_auth) return Status::argument;
    active_auth = &request;
    const auto code = static_cast<Status>(tfw_check_run(path, nullptr));
    active_auth = nullptr;
    return code;
}
}}
#endif
