// Host bridge: runs the real main stage 2 (target/stage2_menu.cpp) against a
// scripted ABI-2 stage 1 (frames into a host framebuffer, key events from a
// list, 5 ms per poll, optional link loss). Stage 2 keeps its .data between
// calls in one loaded library, as on the device.
#include "../target/xyf_stage2.hpp"
extern "C" uint32_t xyf_stage2_main(const xyf::stage2::Abi* abi) __asm__("xyf_stage2_main");
namespace {
uint16_t* fb_ptr = 0;
const uint32_t* events = 0;
uint32_t event_count = 0, event_at = 0, lost_at = 0xffffffffu, frames = 0, time_ms = 0, polls = 0;
uint16_t* begin_frame() { return fb_ptr; }
void end_frame() { ++frames; }
uint32_t next_event() {
    time_ms += 5;
    if (polls++ == lost_at) return xyf::stage2::event_link_lost;
    return event_at < event_count ? events[event_at++] : 0;
}
uint32_t now_ms() { return time_ms; }
const xyf::stage2::Services services = {4, begin_frame, end_frame, next_event, now_ms};
}
extern "C" {
// events: key | pressed << 8 (event_key is added here). lost: poll index at
// which the link is lost (0xffffffff: never). abi_version: what this fake
// stage 1 claims. Returns stage 2's exit; *frames_out = frames presented,
// *ms_out = simulated run time.
unsigned s2_run(uint16_t* fb, const unsigned* keys, unsigned count, unsigned lost, const unsigned char* blob,
                unsigned blob_bytes, unsigned features, const char* note, unsigned abi_version, unsigned* frames_out,
                unsigned* ms_out) {
    static uint32_t scripted[256];
    for (unsigned i = 0; i != count && i != 256; ++i) scripted[i] = keys[i] | xyf::stage2::event_key;
    fb_ptr = fb;
    events = scripted;
    event_count = count > 256 ? 256 : count;
    event_at = 0;
    lost_at = lost;
    frames = 0;
    time_ms = 0;
    polls = 0;
    xyf::stage2::Abi2 abi2;
    abi2.v1.abi = abi_version;
    abi2.v1.bytes = sizeof(abi2);
    abi2.v1.role = 1;
    abi2.v1.stage1_version = 2;
    abi2.v1.key_bitmap[0] = abi2.v1.key_bitmap[1] = abi2.v1.key_bitmap[2] = 0;
    abi2.v1.services = &services;
    abi2.catalog = blob;
    abi2.catalog_bytes = blob_bytes;
    abi2.features = features;
    abi2.note = note;
    const unsigned exit = xyf_stage2_main(&abi2.v1);
    *frames_out = frames;
    *ms_out = time_ms;
    return exit;
}
// ABI-2 layout facts for the test (host pointers are 8 bytes: offsets differ
// from the 32-bit device; the device layout is checked by the cross compiler).
unsigned s2_constant(unsigned which) {
    using namespace xyf::stage2;
    const unsigned values[] = {
        abi_hello,           abi_menu,          request_continue,  request_menu,    request_boot_once,
        request_set_default, request_mtp,       request_uninstall, feature_catalog, feature_set_default,
        feature_mtp,         feature_uninstall, max_rounds};
    return which < sizeof(values) / sizeof(values[0]) ? values[which] : 0xdeadbeefu;
}
}
