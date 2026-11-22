#include "xyf_menu.hpp"
#include "xyf_settings.hpp"
namespace xyf { namespace menu {
namespace {
uint32_t step(uint32_t value, uint32_t count, bool forward) {
    if (!count) return 0;
    if (forward) return value + 1 >= count ? 0 : value + 1;
    return value == 0 ? count - 1 : value - 1;
}
bool usable(const State& s, uint32_t i) { return s.catalog && i < s.catalog->count && (s.catalog->items[i].flags & 1); }
const char* const no_setup = "No mods are saved: the OP-XY already boots without them.";
bool storage_down(const State& s) { return s.storage != storage_unknown && s.storage != storage_ready; }
}

bool needs_storage(const State& s, uint32_t item) { return storage_down(s) && item != item_settings; }

void start(State& s, const catalog::Catalog* catalog, bool has_builtin) {
    for (uint32_t i = 0; i != sizeof(s); ++i)
        reinterpret_cast<char*>(&s)[i] = 0;   // 193: storage unknown (the caller sets it)
    s.catalog = catalog;
    bool any = has_builtin;
    for (uint32_t i = 0; !any && catalog && i != catalog->count; ++i) any = usable(s, i);
    s.screen = any ? Screen::main : Screen::home_empty;
}

const char* main_label(const State& s, uint32_t item) {
    switch (item) {
    case item_continue:
        // 193: while storage starts or is unavailable the row says so (product), never a fallback name.
        if (s.storage == storage_waiting) return "";
        if (s.storage == storage_unavailable) return "Storage unavailable";
        if (storage_down(s)) return "Starting storage...";
        if (s.continue_label) return s.continue_label;
        if (s.catalog && s.catalog->default_index < s.catalog->count)
            return s.catalog->items[s.catalog->default_index].name;
        if (s.storage == storage_ready) return "Continue (no default firmware set)";
        return "Continue (built-in OP-XY)";                                // offline modes: the embedded stock OS
    case item_safe: return s.safe_label ? s.safe_label : "Boot without mods";
    case item_mods: return "Mods";
    case item_settings: return "Settings";
    default: return "";
    }
}

const char* main_note(const State& s) {     // a storage line while storage is down; the safe row's while it stands
    if (s.note && (storage_down(s) || (s.note == no_setup && !s.safe_label))) return s.note;
    return s.main_selected == item_continue && !storage_down(s) ? s.continue_note : 0;   // the saved selection's
}

Action key(State& s, uint32_t k, bool pressed) {
    k = as_play(k);
    if (k == key_shift) {
        s.shift_held = pressed;
        return Action::none;
    }
    if (!pressed) return Action::none;
    const bool minus = k == key_minus, plus = k == key_plus, play = k == key_play;
    if (k == key_stop) {                              // back
#if defined(XYF_SETTING_OPTIONS)
        if (s.screen == Screen::packs && s.pack_confirm) {
            s.pack_confirm = false;
            return Action::redraw;
        }
#endif
        const Screen back = s.screen == Screen::info ? s.info_return :
                            s.screen == Screen::home_empty ? Screen::home_empty :
#if defined(XYF_SETTING_OPTIONS)
                            (s.screen == Screen::options || s.screen == Screen::packs) ? Screen::settings :   // #21: nothing picked
#endif
                            Screen::main;
        if (back == s.screen) return Action::none;
        s.screen = back;
        return Action::redraw;
    }
    switch (s.screen) {
    case Screen::main:
        if (minus || plus) {
            s.note = 0;
            s.main_selected = step(s.main_selected, main_items, plus);
            return Action::redraw;
        }
        if (k - key_track1 < tracks) {
            s.note = 0;
            s.main_selected = k - key_track1;
            return Action::redraw;
        }
        if (!play) return Action::none;
        if (needs_storage(s, s.main_selected)) {     // 193: refused with the reason (Settings still opens)
            s.note = s.storage == storage_unavailable ? "Storage unavailable: the firmware library can't be read." :
                                                        "Storage is still starting.";
            return Action::redraw;
        }
        s.note = 0;
        switch (s.main_selected) {
        case item_continue:                       // #197: converge to what is saved, then boot
            if (s.undo_offer) return Action::undo;   // the status line's offer comes first
            return s.pending ? Action::apply : Action::continue_boot;
        case item_safe:                           // at once: Boot without mods, or Restore
            if (!s.safe_label) {
                s.note = no_setup;
                return Action::redraw;
            }
            return s.safe_restore ? Action::restore : Action::safe_mode;
        case item_mods: return Action::open_mods;
        default:
            s.screen = Screen::settings;
            s.settings_note = 0;
            return Action::redraw;
        }
    case Screen::info:
        if (play) {
            s.screen = s.info_return;
            return Action::redraw;
        }
        return Action::none;
    case Screen::settings: {                      // the extra row opens the installed-pack list on the product
        uint32_t count = settings::rows;
#if defined(XYF_SETTING_OPTIONS)
        if (s.pack_names) ++count;
#endif
        if (minus || plus) {
            s.settings_selected = step(s.settings_selected, count, plus);
            s.settings_note = 0;
            return Action::redraw;
        }
        if (!play) return Action::none;
        if (s.settings_selected != settings::row_about) {
            s.entry = s.settings_selected;
            return Action::change_setting;
        }
        s.info_return = Screen::settings;
        s.screen = Screen::info;   // #21: the About page (once Info on the home menu)
        return Action::redraw;
    }
#if defined(XYF_SETTING_OPTIONS)
    case Screen::packs:
        if (s.pack_busy || s.pack_execute) return Action::none;
        if (s.pack_confirm) {
            if (minus || plus) s.confirm_accept = !s.confirm_accept;
            else if (play) {
                s.pack_execute = s.confirm_accept;
                s.pack_confirm = false;
            } else return Action::none;
        } else if (play && s.shift_held) {
            s.pack_inbox = !s.pack_inbox;
            s.pack_page = 0;
            s.pack_ready = false;
        } else if (!s.pack_ready) return Action::none;
        else if (play && s.pack_count) {
            s.pack_confirm = true;
            s.confirm_accept = false;
        } else if (plus && s.pack_selected + 1 < s.pack_count) ++s.pack_selected;
        else if (minus && s.pack_selected) --s.pack_selected;
        else if (plus && s.pack_more) {
            ++s.pack_page;
            s.pack_ready = false;
        } else if (minus && s.pack_page) {
            --s.pack_page;
            s.pack_ready = false;
        } else return Action::none;
        s.pack_result = 0;
        return Action::redraw;
    case Screen::options:                         // #21: - / + choose, PLAY picks (STOP above: back, unchanged)
        if (minus || plus) {
            s.option_selected = step(s.option_selected, s.option_count, plus);
            return Action::redraw;
        }
        if (!play) return Action::none;
        s.screen = Screen::settings;
        return Action::pick_setting;
#else
    case Screen::packs:
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
}}
