// target/xyf_menu.hpp: the XY Forever menu state machine.
#include "doctest.h"
#include "xyf_menu.hpp"
#include "xyf_settings.hpp"
#include <string.h>
using namespace xyf;
using menu::Action;
using menu::Screen;

namespace {
static Action press(menu::State& s, uint32_t k) {
    const Action a = menu::key(s, k, true);
    menu::key(s, k, false);
    return a;
}
static catalog::Catalog cat;
}  // namespace

TEST_CASE("menu state machine") {
    cat.count = 3;
    cat.default_index = 2;
    strcpy(cat.items[0].id, "granular");
    strcpy(cat.items[0].name, "open-xy: granular");
    cat.items[0].flags = 1;
    strcpy(cat.items[1].id, "noimage");
    strcpy(cat.items[1].name, "noimage");
    cat.items[1].flags = 0;
    strcpy(cat.items[2].id, "opxy-121");
    strcpy(cat.items[2].name, "OP-XY 1.2.1");
    cat.items[2].flags = 1;
    menu::State s;
    menu::start(s, &cat, false);
    REQUIRE((s.screen == Screen::main && s.main_selected == 0));
    REQUIRE(!strcmp(menu::main_label(s, menu::item_continue), "OP-XY 1.2.1"));
    REQUIRE(press(s, menu::key_play) == Action::continue_boot);
    // Navigation wraps both ways; Track keys jump to the four rows (188: no MTP mode row; #21: no Info; #197: no
    // Uninstall row: Continue, the safe-mode row, Mods, Settings).
    REQUIRE((menu::main_items == 4 && menu::item_settings == 3));
    for (uint32_t i = 0; i != menu::main_items; ++i) REQUIRE(!strstr(menu::main_label(s, i), "Uninstall"));
    REQUIRE((press(s, menu::key_minus) == Action::redraw && s.main_selected == menu::item_settings));
    REQUIRE((press(s, menu::key_plus) == Action::redraw && s.main_selected == 0));
    REQUIRE((press(s, menu::key_track1 + 2) == Action::redraw && s.main_selected == menu::item_mods));
    // Mods opens the page (stage 1 runs it; the menu itself stays on main). The row right
    // below the safe-mode row. Keys 10..13 are the knob pushes: PLAY (no TRACK5/6 jumps).
    REQUIRE((press(s, menu::key_track1 + 1) == Action::redraw && press(s, menu::key_plus) == Action::redraw &&
             s.main_selected == menu::item_mods && menu::item_mods == menu::item_safe + 1));
    REQUIRE((press(s, menu::key_track1 + 3) == Action::redraw && s.main_selected == menu::item_settings));
    REQUIRE((press(s, menu::key_track1 + 1) == Action::redraw && press(s, menu::key_plus) == Action::redraw &&
             s.main_selected == menu::item_mods));
    REQUIRE(!strcmp(menu::main_label(s, menu::item_mods), "Mods"));
    REQUIRE((press(s, menu::key_play) == Action::open_mods && s.screen == Screen::main));
    for (uint32_t k = menu::key_knob1; k != menu::key_knob1 + menu::knobs; ++k)    // each knob push: PLAY
        REQUIRE((press(s, k) == Action::open_mods && s.main_selected == menu::item_mods));
    REQUIRE(press(s, menu::key_knob1 + menu::knobs) == Action::none);
    // The safe-mode row (where the Firmware library row was): no setup with mods saved: "Boot without mods", PLAY
    // says why (the line stands while the row is selected); STOP has nothing to go back to.
    press(s, menu::key_track1 + 1);
    REQUIRE((s.main_selected == menu::item_safe && !strcmp(menu::main_label(s, menu::item_safe), "Boot without mods")));
    REQUIRE((press(s, menu::key_play) == Action::redraw && s.screen == Screen::main && menu::main_note(s) &&
             !strcmp(menu::main_note(s), "No mods are saved: the OP-XY already boots without them.")));
    REQUIRE((press(s, menu::key_plus) == Action::redraw && !menu::main_note(s)));
    // With a setup: safe_mode at once (#197: no confirmation, the home menu stays); in safe mode it restores at once.
    s.safe_label = "Boot without mods";
    press(s, menu::key_track1 + 1);
    REQUIRE((press(s, menu::key_play) == Action::safe_mode && s.screen == Screen::main));
    REQUIRE((press(s, menu::key_stop) == Action::none && s.screen == Screen::main));
    s.safe_label = "Restore Open-XY (2 mods)";
    s.safe_restore = true;
    REQUIRE(!strcmp(menu::main_label(s, menu::item_safe), "Restore Open-XY (2 mods)"));
    REQUIRE((press(s, menu::key_play) == Action::restore && s.screen == Screen::main));
    s.safe_label = 0;
    s.safe_restore = false;
    // The first row while a content change's source is missing (undo_offer): PLAY undoes it first.
    press(s, menu::key_track1);
    s.undo_offer = true;
    s.pending = true;
    REQUIRE((press(s, menu::key_play) == Action::undo && s.screen == Screen::main));
    s.undo_offer = false;                         // #197: pending ("(to apply)", "(rebuild)", a flip): it converges
    REQUIRE((press(s, menu::key_play) == Action::apply && s.screen == Screen::main));
    // #197: the saved selection cannot be built: the first row's status line says why while it is selected (a refused
    // row's line first); PLAY still converges (it fails with that reason).
    s.continue_note = "Too big for the mods memory: turn some mods off.";
    REQUIRE((menu::main_note(s) == s.continue_note && press(s, menu::key_play) == Action::apply));
    press(s, menu::key_plus);
    REQUIRE(!menu::main_note(s));
    press(s, menu::key_play);
    REQUIRE(!strcmp(menu::main_note(s), "No mods are saved: the OP-XY already boots without them."));
    press(s, menu::key_minus);
    REQUIRE(menu::main_note(s) == s.continue_note);
    s.pending = false;
    s.continue_note = 0;
    // #21: Settings: Mod memory, Menu at power-on, USB mode, Ask before USB control (#65), About. PLAY on a setting is change_setting with its
    // row; About opens the About page (the home menu has no Info row) and returns to Settings.
    menu::start(s, &cat, false);
    press(s, menu::key_minus);
    REQUIRE(
        (press(s, menu::key_knob1 + 3) == Action::redraw && s.screen == Screen::settings && s.settings_selected == 0));
    press(s, menu::key_stop);
    press(s, menu::key_track1 + 3);       // TRACK4: Settings
    REQUIRE((s.main_selected == menu::item_settings && press(s, menu::key_play) == Action::redraw &&
             s.screen == Screen::settings));
    REQUIRE((settings::rows == 5 && !strcmp(settings::label(settings::row_power_on), "Menu at power-on") &&
             !strcmp(settings::label(settings::row_usb), "USB mode") &&
             !strcmp(settings::label(settings::row_about), "About")));
    REQUIRE((press(s, menu::key_play) == Action::change_setting && s.entry == settings::row_memory));
    press(s, menu::key_plus);
    REQUIRE((press(s, menu::key_play) == Action::change_setting && s.entry == settings::row_power_on));
    press(s, menu::key_plus);
    REQUIRE((press(s, menu::key_play) == Action::change_setting && s.entry == settings::row_usb));
    press(s, menu::key_plus);
    REQUIRE((press(s, menu::key_play) == Action::change_setting && s.entry == settings::row_confirm));
    press(s, menu::key_plus);
    REQUIRE((press(s, menu::key_play) == Action::redraw && s.screen == Screen::info));       // About
    REQUIRE((press(s, menu::key_play) == Action::redraw && s.screen == Screen::settings));
    press(s, menu::key_play);
    REQUIRE((press(s, menu::key_stop) == Action::redraw && s.screen == Screen::settings));
    REQUIRE((press(s, menu::key_stop) == Action::redraw && s.screen == Screen::main));
    // #21: the option list the caller opens on change_setting (any number of options): - / + move and wrap, PLAY
    // picks (pick_setting, back on Settings), STOP leaves it unchanged; the heading's row stays selected.
    {
        static const char* const three[] = {"One", "Two", "Three"};
        press(s, menu::key_play);                                       // Settings (still the row), on About
        press(s, menu::key_minus);
        press(s, menu::key_minus);
        press(s, menu::key_minus);
        REQUIRE((press(s, menu::key_play) == Action::change_setting && s.entry == settings::row_power_on));
        menu::open_options(s, three, 3, 1);
        REQUIRE(
            (s.screen == Screen::options && s.option_count == 3 && s.option_current == 1 && s.option_selected == 1));
        REQUIRE((press(s, menu::key_plus) == Action::redraw && s.option_selected == 2));
        REQUIRE((press(s, menu::key_plus) == Action::redraw && s.option_selected == 0));   // wraps
        REQUIRE((press(s, menu::key_minus) == Action::redraw && s.option_selected == 2));
        REQUIRE((press(s, menu::key_track1) == Action::none && press(s, menu::key_shift) == Action::none));
        REQUIRE((press(s, menu::key_stop) == Action::redraw && s.screen == Screen::settings)); // back: nothing picked
        REQUIRE((s.settings_selected == settings::row_power_on && s.option_current == 1));
        press(s, menu::key_play);
        menu::open_options(s, three, 3, 1);
        press(s, menu::key_minus);
        REQUIRE((press(s, menu::key_knob1) == Action::pick_setting && s.screen == Screen::settings &&
                 s.entry == settings::row_power_on && s.option_selected == 0));
        static const char* const ten[] = {"0", "1", "2", "3", "4", "5", "6", "7", "8", "9"};
        menu::open_options(s, ten, 10, 9); // bounded: max_options, current in range
        REQUIRE((s.option_count == menu::max_options && s.option_current == 0));
        press(s, menu::key_stop);
        press(s, menu::key_stop);
        REQUIRE(s.screen == Screen::main);
    }
    // No usable firmware and nothing built in: home screen (188: one row, About).
    cat.items[0].flags = cat.items[2].flags = 0;
    menu::start(s, &cat, false);
    REQUIRE((s.screen == Screen::home_empty && press(s, menu::key_plus) == Action::none));
    REQUIRE((press(s, menu::key_play) == Action::redraw && s.screen == Screen::info));
    REQUIRE((press(s, menu::key_play) == Action::redraw && s.screen == Screen::home_empty));
    press(s, menu::key_play);
    REQUIRE((press(s, menu::key_stop) == Action::redraw && s.screen == Screen::home_empty));
    menu::start(s, &cat, true);
    REQUIRE(s.screen == Screen::main); // built-in copy counts
    menu::start(s, 0, true);
    REQUIRE(!strcmp(menu::main_label(s, menu::item_continue), "Continue (built-in OP-XY)"));
    press(s, menu::key_track1 + 1);
    REQUIRE((press(s, menu::key_play) == Action::redraw && s.screen == Screen::main &&
             menu::main_note(s))); // no record here
    REQUIRE((menu::key(s, menu::key_play, false) == Action::none && menu::key(s, 99, true) == Action::none));
    // 193: while storage starts the first row says so and nothing that needs the library opens; Settings does.
    menu::start(s, 0, true);
    REQUIRE(s.storage == menu::storage_unknown);
    s.storage = menu::storage_waiting;
    REQUIRE(!strcmp(menu::main_label(s, menu::item_continue), ""));
    s.storage = menu::storage_starting;
    REQUIRE(!strcmp(menu::main_label(s, menu::item_continue), "Starting storage..."));
    REQUIRE((press(s, menu::key_play) == Action::redraw && s.screen == Screen::main &&
             !strcmp(s.note, "Storage is still starting.")));
    for (uint32_t row = menu::item_safe; row != menu::item_settings; ++row) {
        s.main_selected = row;
        s.note = 0;
        REQUIRE((menu::needs_storage(s, row) && press(s, menu::key_play) == Action::redraw &&
                 s.screen == Screen::main && s.note));
    }
    REQUIRE(!menu::needs_storage(s, menu::item_settings));
    press(s, menu::key_plus);
    REQUIRE((s.note == 0 && press(s, menu::key_play) == Action::redraw && s.screen == Screen::settings));
    press(s, menu::key_stop);
    s.storage = menu::storage_checking;
    REQUIRE(!strcmp(menu::main_label(s, 0), "Starting storage..."));
    s.storage = menu::storage_unavailable;
    s.main_selected = menu::item_continue;
    REQUIRE((!strcmp(menu::main_label(s, 0), "Storage unavailable") && press(s, menu::key_play) == Action::redraw &&
             !strcmp(s.note, "Storage unavailable: the firmware library can't be read.")));
    menu::start(s, &cat, true);
    s.storage = menu::storage_ready; // the library known: as before
    REQUIRE((!menu::needs_storage(s, menu::item_safe) && press(s, menu::key_play) == Action::continue_boot));
    cat.default_index = ~0u;
    REQUIRE(!strcmp(menu::main_label(s, 0), "Continue (no default firmware set)"));
}
