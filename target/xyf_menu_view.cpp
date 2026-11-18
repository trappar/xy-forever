#include "xyf_menu_view.hpp"
#include "ui_pages.hpp"
#include "ui_screens.hpp"
#include "xyf_settings.hpp"
// Device: initialized data, not BSS (the cold link has no zero-initialization).
#if defined(__bfin__)
#define XYF_DATA __attribute__((section(".data")))
#else
#define XYF_DATA
#endif
namespace xyf { namespace menu {
namespace {
const char* const hint_main = "- / +  choose      PLAY  select";
const char* const hint_unavailable = "Turn the OP-XY off and on. If it stays, hold COM at power-on.";   // 193
const char* const hint_library = "- / +  choose   PLAY  boot once   SHIFT+PLAY  default   STOP  back";
}

void draw(uint16_t* fb, const State& s, const char* status, const ui::Chrome* chrome) {
    switch (s.screen) {
    case Screen::main: {
        ui::Menu m;                                   // field by field: no memset in the cold build
        for (uint32_t i = 0; i != ui::max_items; ++i) m.items[i] = i < main_items ? main_label(s, i) : "";
        m.count = main_items; m.selected = s.main_selected;
        // 193: rows that need storage dimmed while it starts (not in its first 400 ms: no flicker on a normal
        // boot) or is unavailable; a refused row's line in place of the status; the way out when unavailable.
        m.dimmed = 0;
        for (uint32_t i = 0; i != main_items; ++i)
            if (s.storage != storage_waiting && needs_storage(s, i)) m.dimmed |= 1u << i;
        const bool down = needs_storage(s, item_continue);
        m.status = down && s.note ? s.note : status;
        m.hint = s.storage == storage_unavailable ? hint_unavailable : hint_main; m.chrome = chrome;
        ui::draw_menu(fb, m);
        return;
    }
    case Screen::library: {
        static ui::ListRow rows[catalog::max_entries] XYF_DATA = {};
        static char details[catalog::max_entries][24] XYF_DATA = {};
        const uint32_t count = s.catalog ? s.catalog->count : 0;
        uint32_t shown = 0, selected = 0;
        for (uint32_t i = 0; i != count; ++i) {
            if (i == s.hidden) continue;                 // the spare mods entry (the next build's)
            const catalog::Item& item = s.catalog->items[i];
            if (i == s.library_selected) selected = shown;
            catalog::describe(item, details[shown], sizeof(details[shown]));
            if (i == s.open_xy) {                        // the saved configuration's build: no "content not installed"
                const char* t = "open-xy, saved setup";
                uint32_t k = 0;
                for (; t[k] && k + 1 < sizeof(details[shown]); ++k) details[shown][k] = t[k];
                details[shown][k] = 0;
            }
            rows[shown].title = i == s.open_xy ? "Open-XY" : s.catalog->items[i].name;
            rows[shown].detail = details[shown];
            rows[shown].marked = i == s.catalog->default_index;
            ++shown;
        }
        const ui::List list = {"Firmware library", rows, shown, selected, hint_library, chrome};
        ui::draw_list(fb, list);
        return;
    }
    case Screen::settings: {                             // the compact title bar, as the library
        static ui::ListRow rows[settings::rows] XYF_DATA = {};
        for (uint32_t i = 0; i != settings::rows; ++i) {
            rows[i].title = settings::label(i);
            rows[i].detail = settings::memory_text(s.memory, s.memory_pending);   // the one row
            rows[i].marked = false;
        }
        const ui::List list = {"Settings", rows, settings::rows, s.settings_selected,
                               s.settings_note ? s.settings_note : "PLAY  change   STOP  back", chrome};
        ui::draw_list(fb, list);
        return;
    }
    case Screen::info: {
        // Every slot explicit: a zero-filled tail would compile to memset.
        // The release version as the home page shows it (v0.1.0) and the running image (hello image=).
        const char* version = chrome && chrome->version ? chrome->version : "";
        const char* image = chrome && chrome->image ? chrome->image : "";
        const ui::Message info = {"Info", {"XY Forever", version, image, "Real bootloader: 1.2.1 (never updated)",
                                           "Hold PLAY at power-on to open this menu.", "", "", ""},
                                  5, "PLAY / STOP  back", chrome};
        ui::draw_message(fb, info);
        return;
    }
    case Screen::confirm_uninstall: {
        const ui::Confirm c = {"Uninstall XY Forever?", {"Puts back the stock OP-XY os.ldr and deletes",
                               "xy-forever/ from the device. Your projects stay.", "The real bootloader is not changed.", ""},
                               3, "Cancel", "Uninstall", s.confirm_accept, chrome};
        ui::draw_confirm(fb, c);
        return;
    }
    case Screen::home_empty:
        ui::draw_home_empty(fb, s.home_selected, chrome);
        return;
    case Screen::confirm_save: {
        const ui::Choice c = {"Save changes?", {s.save_blocked ? "These changes cannot be saved:" :
                                                "Save: builds Open-XY if needed, installs its content, then boots it.",
                                                s.save_blocked ? (s.save_note ? s.save_note : "") :
                                                "Discard: forgets the changes and boots the saved setup.",
                                                "Back: returns to the menu; the changes stay.", ""},
                              3, {s.save_blocked ? 0 : "Save", "Discard", "Back"}, s.save_selected, chrome};
        ui::draw_choice(fb, c);
        return;
    }
    }
}
} }
