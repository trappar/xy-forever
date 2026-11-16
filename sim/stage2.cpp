// The simulator's stage-2 trampoline (4.7a1; on the device: target/xyf_stage2_enter.s). Stage 1
// reads, checks and copies the stage-2 file into the emulated window exactly as on the device, but
// that image is Blackfin code; in its place runs the same stage-2 source compiled for the Mac
// (target/stage2_main.cpp, through the same Services3 table). The entry must lie in the copied
// image: stage 1 jumps nowhere else on the device either.
#include "runtime.hpp"
#include "xyf_stage2.hpp"

extern "C" uint32_t xyf_stage2_main(const xyf::stage2::Call* call) __asm__("xyf_stage2_main");
extern "C" uint32_t xyf_stage2_enter(uint32_t entry, const xyf::stage2::Call* call) __asm__("xyf_stage2_enter");

extern "C" uint32_t xyf_stage2_enter(uint32_t entry, const xyf::stage2::Call* call) {
    const xyf::stage2::Header* h = call ? call->header : nullptr;
    if (!h || entry < h->load_address || entry >= h->load_address + h->image_bytes ||
        h->load_address < xyf::stage2::window_begin || h->load_address + h->image_bytes > xyf::stage2::window_end ||
        !xyf_sim::mapped(h->load_address, h->image_bytes))
        xyf_sim::crash("stage 1 jumped to %08x, outside the stage-2 image", entry);
    ++xyf_sim::shared->stage2_calls;
    return xyf_stage2_main(call);
}
