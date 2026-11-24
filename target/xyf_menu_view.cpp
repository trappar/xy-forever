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
}

void draw(uint16_t* fb, const State& s, const char* status, const ui::Chrome* chrome) {
    switch (s.screen) {
    case Screen::main: {
        ui::Menu m;                                   // field by field: no memset in the cold build
        for (uint32_t i = 0; i != ui::max_items; ++i) m.items[i] = i < main_items ? main_label(s, i) : "";
        m.count = main_items;
        m.selected = s.main_selected;
        // 193: rows that need storage dimmed while it starts (not in its first 400 ms: no flicker on a normal
        // boot) or is unavailable; a refused row's line in place of the status; the way out when unavailable.
        // The safe-mode row dimmed too while no setup with mods is saved.
        m.dimmed = s.safe_label ? 0u : 1u << item_safe;
        for (uint32_t i = 0; i != main_items; ++i)
            if (s.storage != storage_waiting && needs_storage(s, i)) m.dimmed |= 1u << i;
        const char* note = main_note(s);
        m.status = note ? note : status;
        m.hint = s.storage == storage_unavailable ? hint_unavailable : hint_main;
        m.chrome = chrome;
        ui::draw_menu(fb, m);
        return;
    }
    case Screen::settings: {                             // the compact title bar
        uint32_t count = settings::rows;
#if defined(XYF_SETTING_OPTIONS)
        static ui::ListRow rows[settings::rows + 1] XYF_DATA = {
            // xyf_settings.hpp Row, labels (+ row_packs)
            {"Mod memory", 0, false},   {"Menu at power-on", 0, false},
            {"USB mode", 0, false},     {"Ask before USB control", 0, false},
            {"Screen saver", 0, false}, {"About", 0, false},
            {"Content packs", 0, false}};
        if (s.pack_names) ++count;
        rows[settings::row_memory].detail = settings::memory_text(s.memory, false);   // pending: marked
        rows[settings::row_power_on].detail = s.power_on_text;     // #21: 0 (blank) without settings.txt
        rows[settings::row_usb].detail = s.usb_text;               // (About: none; its page has the version)
        rows[settings::row_confirm].detail = s.confirm_text;       // #65
        rows[settings::row_saver].detail = s.saver_text;           // #315
        const uint32_t marked = s.settings_marked | (s.memory_pending ? 1u << settings::row_memory : 0u);
        for (uint32_t i = 0; i != settings::rows; ++i) rows[i].marked = marked >> i & 1u;
        // The line: a note, else what a marked value (accent) means: #191, a pick not written yet (saved on its own).
        const char* hint = marked ? "Saving...   STOP  back" : "PLAY  change   STOP  back";
#else
        static ui::ListRow rows[settings::rows] XYF_DATA = {       // xyf_settings.hpp Row, labels
                                                            {"Mod memory", 0, false},
                                                            {"Menu at power-on", 0, false},
                                                            {"USB mode", 0, false},
                                                            {"About", 0, false}};
        rows[settings::row_memory].detail = settings::memory_text(s.memory, s.memory_pending);
        const char* const hint = "PLAY  change   STOP  back";
#endif
        const ui::List list = {"Settings", rows, count, s.settings_selected, s.settings_note ? s.settings_note : hint,
                               chrome,     true};
        ui::draw_list(fb, list);
        return;
    }
    case Screen::packs: {
#if defined(XYF_SETTING_OPTIONS)
        if (s.pack_confirm && s.pack_selected < s.pack_count) {
            ui::Confirm dialog;                       // assign fields to keep the cold build free of memset
            const uint8_t kind = s.pack_kinds ? s.pack_kinds[s.pack_selected] : 0;
            dialog.heading = kind == 1    ? "Import sound pack?" :
                             kind == 2    ? "Enable sound pack?" :
                             kind == 3    ? "Disable sound pack?" :
                             s.pack_inbox ? "Install sound pack?" :
                                            "Remove sound pack?";
            dialog.lines[0] = s.pack_names[s.pack_selected];
            dialog.lines[1] = kind == 2 ? "Not tested on this firmware." : "";   // #195: an imported pack
            dialog.lines[2] = dialog.lines[3] = "";
            dialog.count = kind == 2 ? 2 : 1;
            dialog.cancel = "Cancel";
            dialog.accept = kind == 1    ? "Import" :
                            kind == 2    ? "Enable" :
                            kind == 3    ? "Disable" :
                            s.pack_inbox ? "Install" :
                                           "Remove";
            dialog.accept_selected = s.confirm_accept;
            dialog.chrome = chrome;
            ui::draw_confirm(fb, dialog);
            return;
        }
        static ui::ListRow rows[packs_per_page] XYF_DATA = {};
        for (uint32_t i = 0; i != s.pack_count; ++i) rows[i].title = s.pack_names[i];
        const char* hint = s.pack_importing ? "Importing...   STOP cancel" :
                           s.pack_busy      ? "Working..." :
                           s.pack_result    ? s.pack_result :
                           s.pack_note      ? s.pack_note :
                           s.pack_inbox     ? "PLAY install  SHIFT+PLAY installed  STOP back" :
                                              "PLAY remove  SHIFT+PLAY inbox  STOP back";
        const ui::List list = {
            s.pack_inbox ? "Inbox packs" : "Content packs", rows, s.pack_count, s.pack_selected, hint, chrome, false};
        ui::draw_list(fb, list);
#endif
        return;
    }
    case Screen::options: {                              // #21: a setting's options, the one in effect marked
#if defined(XYF_SETTING_OPTIONS)
        static ui::ListRow rows[max_options] XYF_DATA = {};
        for (uint32_t i = 0; i != s.option_count; ++i) {
            rows[i].title = s.options[i];
            rows[i].detail = i == s.option_current ? "current" : 0;
            rows[i].marked = false;
        }
        const ui::List list = {settings::label(s.settings_selected),
                               rows,
                               s.option_count,
                               s.option_selected,
                               "- / +  choose   PLAY  pick   STOP  back",
                               chrome,
                               true};
        ui::draw_list(fb, list);
#endif
        return;
    }
    case Screen::info: {
        // Every slot explicit: a zero-filled tail would compile to memset.
        // The release version as the home page shows it (v0.1.0) and the running image (hello image=).
        const char* version = chrome && chrome->version ? chrome->version : "";
        const char* image = chrome && chrome->image ? chrome->image : "";
        const ui::Message info = {"About",
                                  {"XY Forever", version, image, "Real bootloader: 1.2.1 (never updated)",
                                   "Hold PLAY at power-on to open this menu.", "", "", ""},
                                  5,
                                  "PLAY / STOP  back",
                                  chrome};
        ui::draw_message(fb, info);
        return;
    }
    case Screen::home_empty: ui::draw_home_empty(fb, chrome); return;
    }
}
}}
