// Host test: XY Forever menu state machine.
#include "xyf_menu.hpp"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
using namespace xyf;
using menu::Action; using menu::Screen;
static unsigned checks;
static void require(bool ok, int line) { ++checks; if (!ok) { fprintf(stderr, "check %u (line %d) failed\n", checks, line); abort(); } }
#define REQUIRE(x) require((x), __LINE__)
static Action press(menu::State& s, uint32_t k) { const Action a = menu::key(s, k, true); menu::key(s, k, false); return a; }
static catalog::Catalog cat;
int main() {
    cat.count = 3; cat.default_index = 2;
    strcpy(cat.items[0].id, "granular"); strcpy(cat.items[0].name, "open-xy: granular"); cat.items[0].flags = 1;
    strcpy(cat.items[1].id, "noimage"); strcpy(cat.items[1].name, "noimage"); cat.items[1].flags = 0;
    strcpy(cat.items[2].id, "opxy-121"); strcpy(cat.items[2].name, "OP-XY 1.2.1"); cat.items[2].flags = 1;
    menu::State s;
    menu::start(s, &cat, false);
    REQUIRE(s.screen == Screen::main && s.main_selected == 0 && s.library_selected == 2);
    REQUIRE(!strcmp(menu::main_label(s, menu::item_continue), "OP-XY 1.2.1"));
    REQUIRE(press(s, menu::key_play) == Action::continue_boot);
    // Navigation wraps both ways; Track keys jump to the first four rows (188: six rows, no MTP mode row).
    REQUIRE(menu::main_items == 6 && menu::item_info == 5);
    REQUIRE(press(s, menu::key_minus) == Action::redraw && s.main_selected == menu::item_info);
    REQUIRE(press(s, menu::key_plus) == Action::redraw && s.main_selected == 0);
    REQUIRE(press(s, menu::key_track1 + 2) == Action::redraw && s.main_selected == menu::item_mods);
    // Mods opens the page (stage 1 runs it; the menu itself stays on main). The row right
    // below Firmware library. Keys 10..13 are the knob pushes: PLAY (no TRACK5/6 jumps).
    REQUIRE(press(s, menu::key_track1 + 1) == Action::redraw && press(s, menu::key_plus) == Action::redraw &&
            s.main_selected == menu::item_mods && menu::item_mods == menu::item_library + 1);
    REQUIRE(press(s, menu::key_track1 + 3) == Action::redraw && s.main_selected == menu::item_uninstall);
    REQUIRE(press(s, menu::key_track1 + 1) == Action::redraw && press(s, menu::key_plus) == Action::redraw &&
            s.main_selected == menu::item_mods);
    REQUIRE(!strcmp(menu::main_label(s, menu::item_mods), "Mods"));
    REQUIRE(press(s, menu::key_play) == Action::open_mods && s.screen == Screen::main);
    for (uint32_t k = menu::key_knob1; k != menu::key_knob1 + menu::knobs; ++k)    // each knob push: PLAY
        REQUIRE(press(s, k) == Action::open_mods && s.main_selected == menu::item_mods);
    REQUIRE(press(s, menu::key_knob1 + menu::knobs) == Action::none);
    // Library: boot once, SHIFT+PLAY sets default, unusable entry does nothing.
    press(s, menu::key_track1 + 1); REQUIRE(press(s, menu::key_play) == Action::redraw && s.screen == Screen::library);
    REQUIRE(press(s, menu::key_plus) == Action::redraw && s.library_selected == 0);
    REQUIRE(press(s, menu::key_play) == Action::boot_once && s.entry == 0);
    REQUIRE(menu::key(s, menu::key_shift, true) == Action::none);
    REQUIRE(press(s, menu::key_play) == Action::set_default && s.entry == 0);
    REQUIRE(press(s, menu::key_knob1 + 2) == Action::set_default && s.entry == 0);   // SHIFT + knob push
    menu::key(s, menu::key_shift, false);
    REQUIRE(press(s, menu::key_knob1 + 1) == Action::boot_once && s.entry == 0);
    press(s, menu::key_plus); REQUIRE(s.library_selected == 1 && press(s, menu::key_play) == Action::none);
    // An entry whose content is not installed (flag 8, not usable): SHIFT+PLAY makes it the
    // default (the content switch first), PLAY asks for a boot once (stage 1 answers it with a note).
    cat.items[1].flags = catalog::flag_content_missing | 2 | 4;
    REQUIRE(press(s, menu::key_play) == Action::boot_once && s.entry == 1);
    menu::key(s, menu::key_shift, true);
    REQUIRE(press(s, menu::key_play) == Action::set_default && s.entry == 1 && s.screen == Screen::library);
    menu::key(s, menu::key_shift, false);
    cat.items[1].flags = 0;
    REQUIRE(press(s, menu::key_stop) == Action::redraw && s.screen == Screen::main);   // Stop = back
    REQUIRE(press(s, menu::key_stop) == Action::none && s.screen == Screen::main);
    // Uninstall needs an explicit move to the accept button.
    menu::start(s, &cat, false);
    press(s, menu::key_track1 + 3);
    REQUIRE(press(s, menu::key_play) == Action::redraw && s.screen == Screen::confirm_uninstall && !s.confirm_accept);
    REQUIRE(press(s, menu::key_play) == Action::redraw && s.screen == Screen::main);   // default = Cancel
    press(s, menu::key_play); press(s, menu::key_plus);
    REQUIRE(s.confirm_accept && press(s, menu::key_play) == Action::uninstall);
    // Info returns to where it was opened from.
    menu::start(s, &cat, false); press(s, menu::key_minus); press(s, menu::key_knob1 + 3);
    REQUIRE(s.screen == Screen::info && press(s, menu::key_play) == Action::redraw && s.screen == Screen::main);
    // No usable firmware and nothing built in: home screen (188: one row, Info).
    cat.items[0].flags = cat.items[2].flags = 0;
    menu::start(s, &cat, false);
    REQUIRE(s.screen == Screen::home_empty && press(s, menu::key_plus) == Action::none);
    REQUIRE(press(s, menu::key_play) == Action::redraw && s.screen == Screen::info);
    REQUIRE(press(s, menu::key_play) == Action::redraw && s.screen == Screen::home_empty);
    press(s, menu::key_play); REQUIRE(press(s, menu::key_stop) == Action::redraw && s.screen == Screen::home_empty);
    menu::start(s, &cat, true);  REQUIRE(s.screen == Screen::main);                   // built-in copy counts
    menu::start(s, 0, true);
    REQUIRE(!strcmp(menu::main_label(s, menu::item_continue), "Continue (built-in OP-XY)"));
    press(s, menu::key_track1 + 1); press(s, menu::key_play);
    REQUIRE(s.screen == Screen::library && press(s, menu::key_play) == Action::redraw && s.screen == Screen::main);
    REQUIRE(menu::key(s, menu::key_play, false) == Action::none && menu::key(s, 99, true) == Action::none);
    // 193: while storage starts the first row says so and nothing that needs the library opens; Info does.
    menu::start(s, 0, true); REQUIRE(s.storage == menu::storage_unknown); s.storage = menu::storage_waiting;
    REQUIRE(!strcmp(menu::main_label(s, menu::item_continue), ""));
    s.storage = menu::storage_starting;
    REQUIRE(!strcmp(menu::main_label(s, menu::item_continue), "Starting storage..."));
    REQUIRE(press(s, menu::key_play) == Action::redraw && s.screen == Screen::main &&
            !strcmp(s.note, "Storage is still starting."));
    for (uint32_t row = menu::item_library; row != menu::item_info; ++row) {
        s.main_selected = row; s.note = 0;
        REQUIRE(menu::needs_storage(s, row) && press(s, menu::key_play) == Action::redraw && s.screen == Screen::main && s.note);
    }
    REQUIRE(!menu::needs_storage(s, menu::item_info));
    press(s, menu::key_plus); REQUIRE(s.note == 0 && press(s, menu::key_play) == Action::redraw && s.screen == Screen::info);
    press(s, menu::key_stop);
    s.storage = menu::storage_checking; REQUIRE(!strcmp(menu::main_label(s, 0), "Starting storage..."));
    s.storage = menu::storage_unavailable; s.main_selected = menu::item_continue;
    REQUIRE(!strcmp(menu::main_label(s, 0), "Storage unavailable") && press(s, menu::key_play) == Action::redraw &&
            !strcmp(s.note, "Storage unavailable: the firmware library can't be read."));
    menu::start(s, &cat, true); s.storage = menu::storage_ready;       // the library known: as before
    REQUIRE(!menu::needs_storage(s, menu::item_library) && press(s, menu::key_play) == Action::continue_boot);
    cat.default_index = ~0u; REQUIRE(!strcmp(menu::main_label(s, 0), "Continue (no default firmware set)"));
    printf("%u checks passed\n", checks);
    return 0;
}
