#ifndef XYF_MENU_HPP
#define XYF_MENU_HPP
#include <stdint.h>
#include "xyf_catalog.hpp"
// XY Forever menu logic (processor b): a pure state machine. Keys in, an
// action out, and a model the UI pages draw. No stock calls, no I/O.
namespace xyf { namespace menu {
// Key enum indices from the stock bootloader table (KLeft/KRight are the
// physical "-" / "+" keys). Stop = back on every screen. A push of any of the four knobs
// (KEncClick1..4 = key_knob1..+3) is PLAY everywhere (SHIFT + push = SHIFT + PLAY); the turns are not keys here.
enum Key : uint32_t { key_minus = 53, key_plus = 54, key_play = 51, key_stop = 52, key_shift = 55, key_track1 = 6,
                      key_knob1 = 10, knobs = 4, tracks = 4 };
// A knob push as PLAY; any other key unchanged.
inline uint32_t as_play(uint32_t k) { return k - key_knob1 < knobs ? uint32_t(key_play) : k; }
// Or'd into - / + for a knob turn's row (key_ui turn_knobs; the Mods page only: SHIFT + turn = its filter).
static const uint32_t key_turn = 0x100u;
// options (#21): a setting's option list, opened from the Settings page by the caller (open_options). The offline
// stage-2 modes build without it (XYF_SETTING_OPTIONS): their narrow stage 1 (XYF_SMALL_CRC, xyf_build.py) has
// no settings.txt and no room; their Settings page keeps its rows (PLAY answers nothing there).
#if !defined(XYF_SMALL_CRC)
#define XYF_SETTING_OPTIONS 1
#endif
enum class Screen : uint32_t { main, library, info, confirm_uninstall, home_empty, confirm_save, settings, options };
enum class Action : uint32_t {
    none,
    redraw,          // state changed: draw again
    continue_boot,   // boot the default (or the built-in copy)
    boot_once,       // boot library entry `entry` once
    set_default,     // make library entry `entry` the default (needs a write)
    uninstall,       // confirmed: put back stock os.ldr, delete xy-forever/
    open_mods,       // open the Mods page (xyf_mods; stage 1 runs it)
    save,            // the Save dialog's Save: apply the pending configuration, then boot it
    discard,         // the Save dialog's Discard: forget the pending changes, boot the saved configuration
    change_setting,  // the Settings page's PLAY on setting `entry` (xyf_settings.hpp Row): the caller opens its options
    pick_setting     // #21: the option list's PLAY: setting `entry` takes option_selected (back on the Settings page)
};
// The Save dialog's buttons.
enum SaveChoice : uint32_t { choice_save = 0, choice_discard = 1, choice_back = 2 };
// Main menu rows, in order (Mods right below Firmware library).
// 188: no MTP mode row (MTP is always on with the composite USB device). #21: no Info row (Settings' About).
enum MainItem : uint32_t { item_continue = 0, item_library = 1, item_mods = 2, item_uninstall = 3,
                           item_settings = 4, main_items = 5 };
// 193: what B knows about A's storage, set by the caller every pass (product: xyf_boot::storage_view).
// unknown: not reported (the offline modes, whose stock OS is embedded: "Continue (built-in OP-XY)");
// waiting: the first 400 ms of A's mount (the first row stays blank: a normal library arrives in about
// 250 ms); starting / checking (a long mount: the full scan after an unclean shutdown) / repairing: the
// first row says "Starting storage...", nothing that needs the library opens; unavailable: the library
// cannot be read this boot (nothing can boot), the first row says so. ready: the library is known.
enum StorageView : uint32_t { storage_unknown = 0, storage_ready, storage_waiting, storage_starting, storage_checking,
                              storage_repairing, storage_unavailable };
// TRACK1..TRACK4 (keys 6..9) jump to the first four rows: Continue, Firmware library, Mods, Uninstall. (Keys 10 and 11, once taken for TRACK5/6, are knob pushes.)
struct State {
    Screen screen, info_return;       // info_return: screen PLAY goes back to from Info
    uint32_t main_selected, library_selected;   // (the "no firmware" home has one row: no selection)
    bool confirm_accept;              // which button the confirm dialog highlights
    bool shift_held;
    const catalog::Catalog* catalog;  // may be null (library not loaded)
    uint32_t entry;                   // entry for boot_once / set_default
    // Set by the caller (xyf_mods_io) every pass: changes are pending (Continue asks to save them first);
    // save_blocked: they cannot be saved (the dialog offers Discard / Back and says save_note); the first
    // row's text (0: the default's name); in the library, the catalog entry shown as "Open-XY" and the spare
    // mods entry not shown (~0u: none).
    uint32_t save_selected;
    bool pending, save_blocked;
    const char* continue_label;
    const char* save_note;
    uint32_t open_xy, hidden;
    // The Settings page: its row and its line. The Mod memory row (xyf_settings.hpp Memory) and its
    // pending mark, set by the caller (xyf_mods_io menu_state) every pass.
    uint32_t settings_selected;
    const char* settings_note;
    uint32_t memory;
    bool memory_pending;
#if defined(XYF_SETTING_OPTIONS)
    // #21: the settings.txt rows' values, set by the caller every pass (usb_service settings_view; xyf_settings.hpp
    // power_on_text, usb_text); 0: blank. Texts, not values: the offline stage-2 modes have no settings.txt and no
    // room for its texts. (A newer record's line is settings_note, set there too.)
    const char *power_on_text, *usb_text;
    // #21: rows marked (a bit per xyf_settings.hpp Row; the value in accent): a settings.txt row changed and not saved
    // yet (saved once, when the page is left), Mod memory pending until boot (memory_pending). Set by the caller.
    uint32_t settings_marked;
    // #21: the option list (Screen::options; open_options): the setting's option texts, how many, the one in effect
    // (marked "current") and the selected one. The heading is the label of the Settings row (settings_selected).
    const char* const* options;
    uint32_t option_count, option_current, option_selected;
#endif
    // 193: StorageView (the caller's, after start() and every pass) and the line a refused row left (0: none).
    uint32_t storage;
    const char* note;
};
// 193: the row needs the library (shown dimmed, PLAY refused while storage is not ready). Settings never does
// (its About row; a setting that needs A says so).
bool needs_storage(const State& state, uint32_t item);
// Start screen: the main menu, or the "no firmware" home when no catalog
// entry is usable and there is nothing built in to continue to.
void start(State& state, const catalog::Catalog* catalog, bool has_builtin);
// One key event (pressed = 1 on press, 0 on release). Returns the action.
Action key(State& state, uint32_t key, bool pressed);
// Label of a main-menu row (e.g. "Continue: OP-XY 1.2.1").
const char* main_label(const State& state, uint32_t item);
#if defined(XYF_SETTING_OPTIONS)
// #21: the caller's answer to change_setting: the setting's options (texts[current] in effect, selected first).
static const uint32_t max_options = 8;
inline void open_options(State& s, const char* const* texts, uint32_t count, uint32_t current) {
    s.options = texts; s.option_count = count < max_options ? count : max_options;
    s.option_current = s.option_selected = current < s.option_count ? current : 0;
    s.screen = Screen::options;
}
#endif
} }
#endif
