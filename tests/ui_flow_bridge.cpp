// Host bridge: drive the menu state machine with keys and render each step.
#include "../target/xyf_menu_view.hpp"
#include "../target/xyf_settings.hpp"
#include <string.h>
using namespace xyf;
static catalog::Catalog cat;
static menu::State flow_state;
extern xyf::ui::Chrome bridge_chrome;                 // ui_preview_bridge.cpp
extern "C" {
// mode: 0 = built-in copy only, 1 = library + built-in, 2 = nothing bootable.
void flow_start(unsigned mode) {
    memset(&cat, 0, sizeof(cat));
    cat.count = 4; cat.default_index = 1;
    const char* ids[] = {"granular", "opxy-121", "opxy-125", "noimage"};
    const char* names[] = {"open-xy: granular", "OP-XY 1.2.1", "OP-XY 1.2.5", "My test build"};
    const uint8_t kinds[] = {2, 1, 1, 3}, flags[] = {7, 1, 1, 0};
    for (unsigned i = 0; i != 4; ++i) {
        strcpy(cat.items[i].id, ids[i]); strcpy(cat.items[i].name, names[i]);
        cat.items[i].kind = kinds[i]; cat.items[i].flags = flags[i];
    }
    cat.items[0].machines = 6;
    menu::start(flow_state, mode == 1 ? &cat : 0, mode != 2);
}
unsigned flow_key(unsigned key) { const unsigned a = unsigned(menu::key(flow_state, key, true)); menu::key(flow_state, key, false); return a; }
void flow_hold(unsigned key, unsigned pressed) { menu::key(flow_state, key, pressed != 0); }
// the Settings page's line; its Mod memory row (settings::Memory) and pending mark, as key_ui and
// xyf_mods_io menu_state set them.
void flow_settings(unsigned memory, unsigned pending, const char* note) {
    flow_state.memory = memory; flow_state.memory_pending = pending != 0;
    flow_state.settings_note = note && *note ? note : 0;
}
// #21: the settings.txt rows (usb_service settings_view): Menu at power-on, USB mode, a newer record.
void flow_settings_rows(unsigned power_on, unsigned usb, unsigned newer) {
    flow_state.power_on_text = settings::power_on_text(power_on); flow_state.usb_text = settings::usb_text(usb);
    flow_state.confirm_text = settings::confirm_text(usb & 1);           // #65: On / Off
    if (newer) flow_state.settings_note = settings::note_newer;
}
// #21: the rows' marks (settings_view: not saved yet; bit per settings::Row) and the option list key_ui opens on
// change_setting for the selected row (current: the value in effect).
void flow_settings_marked(unsigned marks) { flow_state.settings_marked = marks; }
void flow_options(unsigned current) {
    const unsigned row = flow_state.settings_selected;
    const bool memory = row == settings::row_memory, power_on = row == settings::row_power_on;
    menu::open_options(flow_state, memory ? settings::memory_options : power_on ? settings::power_on_options : settings::usb_options,
                       memory ? 2 : power_on ? settings::power_on_option_count : settings::usb_option_count, current);
}
unsigned flow_option() { return flow_state.option_selected; }
void flow_storage(unsigned view) { flow_state.storage = view; }      // 193: xyf::menu::StorageView
void flow_draw(uint16_t* fb, const char* status) { menu::draw(fb, flow_state, status, &bridge_chrome); }
// the saved configuration as xyf_mods_io::menu_state sets it (#197: pending, the first row's note).
void flow_config(const char* label, unsigned pending, const char* note) {
    flow_state.continue_label = label && *label ? label : 0;
    flow_state.pending = pending != 0;
    flow_state.continue_note = note && *note ? note : 0;
}
// The safe-mode row as menu_state sets it (label "": no setup with mods saved) and whether it restores.
void flow_safe(const char* label, unsigned restore) {
    flow_state.safe_label = label && *label ? label : 0; flow_state.safe_restore = restore != 0;
}
unsigned flow_screen() { return unsigned(flow_state.screen); }
}
