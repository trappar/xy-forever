// XY Forever main stage 2 (ABI 2, --xyf-stage2-menu): the full XY Forever
// menu (xyf_menu state machine + xyf_menu_view pages) running from
// /yaffs2/xy-forever/stage2/main.xys2 instead of inside stage 1. Linked at
// xyf::stage2::window_begin with xyf_stage2_main first.
//
// Everything goes through the ABI-2 table stage 1 provides: frames, key
// events, time, the catalog blob (decoded into stage 2's own copy) and a note.
// Every action is returned to stage 1 as a request (code | entry << 8), which
// performs it with its own audited code, or calls stage 2 again with a note.
// Stage 2 keeps its menu state in its own .data between calls.
// Freestanding: no stock calls, no storage, no BSS, no libgcc.
#include "xyf_stage2.hpp"
#include "xyf_menu_view.hpp"

#if defined(__bfin__)
#define XYF_ENTRY __attribute__((section(".text.entry"), used))
#define XYF_DATA __attribute__((section(".data")))
#else
#define XYF_ENTRY __attribute__((used))
#define XYF_DATA
#endif

extern "C" uint32_t xyf_stage2_main(const xyf::stage2::Abi* abi) __asm__("xyf_stage2_main") XYF_ENTRY;

namespace {
using namespace xyf;
const uint32_t idle_ms = 60000;             // as the stage-1 menu: no keys for 60 s -> boot the default
const ui::Chrome chrome = {"stage 2 (ABI 2)", false, 0, false, ""};   // the version line; no USB mark here

catalog::Catalog catalog_copy XYF_DATA = {};
menu::State menu_state XYF_DATA = {};
uint32_t started XYF_DATA = 0;

uint32_t request(stage2::Request code, uint32_t entry) { return uint32_t(code) | entry << 8; }
}

extern "C" uint32_t xyf_stage2_main(const stage2::Abi* abi) {
    if (!abi || abi->abi != stage2::abi_menu || abi->bytes < sizeof(stage2::Abi2) || !abi->services ||
        abi->services->count < 4)
        return stage2::exit_menu;                       // unknown stage 1: let it show its own menu
    const stage2::Abi2& a = *reinterpret_cast<const stage2::Abi2*>(abi);
    const stage2::Services& s = *abi->services;
    const bool have = (a.features & stage2::feature_catalog) && a.catalog && a.catalog_bytes &&
                      catalog::decode(a.catalog, a.catalog_bytes, catalog_copy);
    if (!started) {
        menu::start(menu_state, have ? &catalog_copy : 0, true);
        started = 1;
    } else {
        // Called again after a request stage 1 answered with a note: back to
        // the main page to show it (as the stage-1 menu does), with the
        // catalog stage 1 has now (refreshed, e.g. after set default).
        menu_state.catalog = have ? &catalog_copy : 0;
        menu_state.screen = menu::Screen::main;
        menu_state.shift_held = false;                       // its release may have reached stage 1
    }
    const char* note = a.note;
    uint32_t last_key = s.now_ms();
    bool dirty = true;
    for (;;) {
        if (dirty) {
            if (uint16_t* fb = s.begin_frame()) {
                menu::draw(fb, menu_state, note, &chrome);
                s.end_frame();
                dirty = false;
            }
        }
        const uint32_t event = s.next_event();
        if (event == stage2::event_link_lost) return stage2::exit_continue;   // stage 1 boots built-in
        if (event & stage2::event_key) {
            last_key = s.now_ms();
            const menu::Action action = menu::key(menu_state, event & 0xffu, (event >> 8 & 1u) != 0);
            switch (action) {
            case menu::Action::none: break;
            case menu::Action::redraw: dirty = true; break;
            case menu::Action::continue_boot: return request(stage2::request_continue, 0);
            case menu::Action::open_mods: return stage2::exit_menu;     // stage 1 runs the Mods page
            case menu::Action::safe_mode: case menu::Action::restore:
            case menu::Action::undo: case menu::Action::apply: return stage2::exit_menu;   // stage 1's (never here: no record)
            case menu::Action::change_setting: case menu::Action::pick_setting: break;   // stage 1's Settings (no record here)
            }
        }
        if (s.now_ms() - last_key > idle_ms) return request(stage2::request_continue, 0);
    }
}
