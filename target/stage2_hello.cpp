// XY Forever "hello" stage 2 (test image, --xyf-stage2). Linked at
// xyf::stage2::window_begin with this function first, packed into
// /yaffs2/xy-forever/stage2/main.xys2 by xy_forever.xyf_stage2.pack.
// Draws one screen through the ABI services, then returns exit_continue on
// PLAY, after 60 s without keys, or at once if the processor link is lost.
// Freestanding: no stock calls, no storage, no BSS, no libgcc.
#include "xyf_stage2.hpp"
#include "ui_gfx.hpp"

extern "C" uint32_t xyf_stage2_main(const xyf::stage2::Abi* abi)
    __asm__("xyf_stage2_main") __attribute__((section(".text.entry"), used));

namespace {
using namespace xyf;
const uint32_t play_key = 51, timeout_ms = 60000, frame_tries = 50;

void draw(uint16_t* fb) {
    gfx::clear(fb, gfx::black);
    gfx::logo(fb, 24, 20, gfx::accent, gfx::white);
    gfx::text(fb, 24, 92, "Stage 2 is running.", gfx::white, 2);
    gfx::text(fb, 24, 124, "Loaded from /yaffs2/xy-forever/stage2/main.xys2", gfx::grey, 1);
    gfx::text(fb, 24, 136, "(hello test image: no storage access, no writes)", gfx::grey, 1);
    gfx::text(fb, 24, 168, "PLAY  start the OP-XY", gfx::white, 2);
    gfx::text(fb, 24, 200, "Continues by itself after 60 s.", gfx::grey, 1);
}
}

extern "C" uint32_t xyf_stage2_main(const stage2::Abi* abi) {
    if (!abi || abi->abi != stage2::abi || abi->bytes < sizeof(stage2::Abi) || !abi->services ||
        abi->services->count < 4)
        return stage2::exit_menu;                       // unknown stage 1: let it show its own menu
    const stage2::Services& s = *abi->services;
    const uint32_t start = s.now_ms();
    bool drawn = false;
    for (uint32_t tries = 0;;) {
        if (!drawn && tries < frame_tries) {
            ++tries;
            if (uint16_t* fb = s.begin_frame()) { draw(fb); s.end_frame(); drawn = true; }
        }
        const uint32_t event = s.next_event();
        if (event == stage2::event_link_lost) return stage2::exit_continue;
        if ((event & stage2::event_key) && (event & 0xffu) == play_key && (event >> 8 & 1u))
            return stage2::exit_continue;
        if (s.now_ms() - start > timeout_ms) return stage2::exit_continue;
    }
}
