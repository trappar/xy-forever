// XY Forever stage 2, product image (4.7a1, ABI 3; target/xyf_stage2.hpp). Linked at
// xyf::stage2::window_begin with this function first, packed by xyf_build.py --xyf-product as
// main.xys2 (device path /yaffs2/xy-forever/stage2/main.xys2). Stage 1 (B's USB service) calls
// it with op_init once, op_turn every home_pump pass and op_command for a request type stage 1
// does not answer. Payload: stage2.info. Freestanding: no stock calls, no storage, no BSS, no
// libgcc; it reaches stage 1 only through the Services3 table it is handed.
#include "xyf_stage2.hpp"
#include "usb_proto_ids.hpp"

extern "C" uint32_t xyf_stage2_main(const xyf::stage2::Call* call)
    __asm__("xyf_stage2_main") __attribute__((section(".text.entry"), used));

namespace {
namespace S2 = xyf::stage2;
// Initialized data, not BSS (the stage-2 link has none). op_init sets every field.
uint32_t turns __attribute__((section(".data"))) = 0;
void put32(uint8_t* p, uint32_t v) { p[0] = uint8_t(v); p[1] = uint8_t(v >> 8); p[2] = uint8_t(v >> 16); p[3] = uint8_t(v >> 24); }

// stage2.info: u32 status, u32 stage-2 version, 8 bytes of the image SHA-256, u32 services
// version, u32 loop turns seen since init. Any request bytes: bad_request (status only).
uint32_t info(const S2::Call& c) {
    uint8_t p[24];
    if (c.length) { put32(p, xyf::usbp::s_bad_request); c.services->reply(c.type, c.seq, p, 4); return S2::handled; }
    put32(p, xyf::usbp::s_ok);
    put32(p + 4, c.header->version);
    for (uint32_t i = 0; i != 8; ++i) p[8 + i] = c.header->image_sha256[i];
    put32(p + 16, c.services->version);
    put32(p + 20, turns);
    c.services->reply(c.type, c.seq, p, sizeof(p));
    return S2::handled;
}
}

extern "C" uint32_t xyf_stage2_main(const S2::Call* c) {
    if (!c || c->abi != S2::abi_service || c->bytes < sizeof(S2::Call) || !c->services || !c->header ||
        c->services->version < S2::services_version || c->services->bytes < sizeof(S2::Services3))
        return 0xffffffffu;                             // not a stage 1 this image knows: no answer it accepts
    switch (c->op) {
    case S2::op_init:
        turns = 0;
        c->services->trace(S2::trace_init, c->header->version);
        return S2::init_ok;
    case S2::op_turn:
        ++turns;
        return S2::turn_ok;
    case S2::op_command:
        return c->type == xyf::usbp::t_stage2_info ? info(*c) : S2::not_handled;
    default:
        return 0xffffffffu;
    }
}
