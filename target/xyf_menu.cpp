#include "xyf_menu.hpp"
#include "xyf_settings.hpp"
namespace xyf { namespace menu {
namespace {
uint32_t step(uint32_t value, uint32_t count, bool forward) {
    if (!count) return 0;
    if (forward) return value + 1 >= count ? 0 : value + 1;
    return value == 0 ? count - 1 : value - 1;
}
uint32_t library_count(const State& s) { return s.catalog ? s.catalog->count : 0; }
// The library's next row, past the hidden (spare mods) entry.
uint32_t library_step(const State& s, uint32_t value, bool forward) {
    const uint32_t count = library_count(s);
    uint32_t next = step(value, count, forward);
    if (next == s.hidden && count > 1) next = step(next, count, forward);
    return next;
}
bool usable(const State& s, uint32_t i) { return s.catalog && i < s.catalog->count && (s.catalog->items[i].flags & 1); }
// Listed with its os.ldr but its content not installed: SHIFT+PLAY installs it (set default); PLAY's
// boot once is answered by stage 1 ("make it the default to install its content").
bool selectable(const State& s, uint32_t i) {
    return usable(s, i) || (s.catalog && i < s.catalog->count && (s.catalog->items[i].flags & catalog::flag_content_missing));
}
bool storage_down(const State& s) { return s.storage != storage_unknown && s.storage != storage_ready; }
}

bool needs_storage(const State& s, uint32_t item) {
    return storage_down(s) && item != item_settings;
}

void start(State& s, const catalog::Catalog* catalog, bool has_builtin) {
    for (uint32_t i = 0; i != sizeof(s); ++i) reinterpret_cast<char*>(&s)[i] = 0;   // 193: storage unknown (the caller sets it)
    s.catalog = catalog;
    s.open_xy = s.hidden = ~0u;
    bool any = has_builtin;
    for (uint32_t i = 0; !any && i != library_count(s); ++i) any = usable(s, i);
    s.screen = any ? Screen::main : Screen::home_empty;
    if (catalog && catalog->default_index < catalog->count) s.library_selected = catalog->default_index;
}

const char* main_label(const State& s, uint32_t item) {
    switch (item) {
    case item_continue:
        // 193: while storage starts or is unavailable the row says so (product), never a fallback name.
        if (s.storage == storage_waiting) return "";
        if (s.storage == storage_unavailable) return "Storage unavailable";
        if (storage_down(s)) return "Starting storage...";
        if (s.continue_label) return s.continue_label;
        if (s.catalog && s.catalog->default_index < s.catalog->count) return s.catalog->items[s.catalog->default_index].name;
        if (s.storage == storage_ready) return "Continue (no default firmware set)";
        return "Continue (built-in OP-XY)";                                // offline modes: the embedded stock OS
    case item_library: return "Firmware library";
    case item_uninstall: return "Uninstall XY Forever";
    case item_mods: return "Mods";
    case item_settings: return "Settings";
    default: return "";
    }
}

Action key(State& s, uint32_t k, bool pressed) {
    k = as_play(k);
    if (k == key_shift) { s.shift_held = pressed; return Action::none; }
    if (!pressed) return Action::none;
    const bool minus = k == key_minus, plus = k == key_plus, play = k == key_play;
    if (k == key_stop) {                              // back
        const Screen back = s.screen == Screen::info ? s.info_return :
                            s.screen == Screen::home_empty ? Screen::home_empty :
#if defined(XYF_SETTING_OPTIONS)
                            s.screen == Screen::options ? Screen::settings :   // #21: nothing picked
#endif
                            Screen::main;
        if (back == s.screen) return Action::none;
        s.screen = back;
        return Action::redraw;
    }
    switch (s.screen) {
    case Screen::main:
        if (minus || plus) { s.note = 0; s.main_selected = step(s.main_selected, main_items, plus); return Action::redraw; }
        if (k - key_track1 < tracks) { s.note = 0; s.main_selected = k - key_track1; return Action::redraw; }
        if (!play) return Action::none;
        if (needs_storage(s, s.main_selected)) {     // 193: refused with the reason (Settings still opens)
            s.note = s.storage == storage_unavailable ? "Storage unavailable: the firmware library can't be read." :
                                                        "Storage is still starting.";
            return Action::redraw;
        }
        s.note = 0;
        switch (s.main_selected) {
        case item_continue:                       // pending changes: the Save dialog first
            if (!s.pending) return Action::continue_boot;
            s.screen = Screen::confirm_save;
            s.save_selected = s.save_blocked ? uint32_t(choice_discard) : uint32_t(choice_save);
            return Action::redraw;
        case item_library: s.screen = Screen::library; return Action::redraw;
        case item_uninstall: s.screen = Screen::confirm_uninstall; s.confirm_accept = false; return Action::redraw;
        case item_mods: return Action::open_mods;
        default: s.screen = Screen::settings; s.settings_note = 0; return Action::redraw;
        }
    case Screen::library: {
        const uint32_t count = library_count(s);
        if (minus || plus) { s.library_selected = library_step(s, s.library_selected, plus); return Action::redraw; }
        if (!play) return Action::none;
        if (!count) { s.screen = Screen::main; return Action::redraw; }       // empty list: PLAY goes back
        if (!selectable(s, s.library_selected)) return Action::none;         // missing os.ldr: nothing to boot
        s.entry = s.library_selected;
        return s.shift_held ? Action::set_default : Action::boot_once;
    }
    case Screen::info:
        if (play) { s.screen = s.info_return; return Action::redraw; }
        return Action::none;
    case Screen::confirm_uninstall:
        if (minus || plus) { s.confirm_accept = !s.confirm_accept; return Action::redraw; }
        if (!play) return Action::none;
        if (s.confirm_accept) return Action::uninstall;
        s.screen = Screen::main;
        return Action::redraw;
    case Screen::confirm_save: {                  // Save / Discard / Back (blocked: Discard / Back)
        const uint32_t first = s.save_blocked ? uint32_t(choice_discard) : uint32_t(choice_save);
        if (minus) { s.save_selected = s.save_selected <= first ? uint32_t(choice_back) : s.save_selected - 1; return Action::redraw; }
        if (plus) { s.save_selected = s.save_selected >= uint32_t(choice_back) ? first : s.save_selected + 1; return Action::redraw; }
        if (!play) return Action::none;
        s.screen = Screen::main;
        if (s.save_selected == choice_save && !s.save_blocked) return Action::save;
        if (s.save_selected == choice_discard) return Action::discard;
        return Action::redraw;
    }
    case Screen::settings:                        // one row per setting; PLAY (or a knob push): its options
        if (minus || plus) { s.settings_selected = step(s.settings_selected, settings::rows, plus); s.settings_note = 0; return Action::redraw; }
        if (!play) return Action::none;
        if (s.settings_selected != settings::row_about) { s.entry = s.settings_selected; return Action::change_setting; }
        s.info_return = Screen::settings; s.screen = Screen::info;   // #21: the About page (once Info on the home menu)
        return Action::redraw;
#if defined(XYF_SETTING_OPTIONS)
    case Screen::options:                         // #21: - / + choose, PLAY picks (STOP above: back, unchanged)
        if (minus || plus) { s.option_selected = step(s.option_selected, s.option_count, plus); return Action::redraw; }
        if (!play) return Action::none;
        s.screen = Screen::settings;
        return Action::pick_setting;
#else
    case Screen::options: break;                  // never opened here
#endif
    case Screen::home_empty:
        if (!play) return Action::none;                // 188: one row, About (MTP is always on)
        s.info_return = Screen::home_empty;
        s.screen = Screen::info;
        return Action::redraw;
    }
    return Action::none;
}
} }
