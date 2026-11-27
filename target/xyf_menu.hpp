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
enum Key : uint32_t {
    key_minus = 53,
    key_plus = 54,
    key_play = 51,
    key_stop = 52,
    key_shift = 55,
    key_track1 = 6,
    key_knob1 = 10,
    knobs = 4,
    tracks = 4
};
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
enum class Screen : uint32_t { main, info, home_empty, settings, options, packs, uninstall };
enum class Action : uint32_t {
    none,
    redraw,          // state changed: draw again
    continue_boot,   // boot the default (or the built-in copy)
    safe_mode,       // "Boot without mods" (at once): the base with its official content, booted (the setup is kept)
    restore,         // "Restore Open-XY": the saved Open-XY back as the default, booted
    open_mods,       // open the Mods page (xyf_mods; stage 1 runs it)
    change_setting, // the Settings page's PLAY on setting `entry` (xyf_settings.hpp Row): the caller opens its options
    pick_setting, // #21: the option list's PLAY: setting `entry` takes option_selected (back on the Settings page)
    uninstall,
    undo, // the first row while a content change's source is missing (State::undo_offer): undo it
    apply // the first row while the saved setup is not applied (pending): converge to it, then boot
};
// Main menu rows, in order. 188: no MTP mode row (MTP is always on with the composite USB device). #21: no Info
// row (Settings' About). The safe-mode row (State::safe_label) sits where the Firmware library row was. Uninstall is in Settings.
enum MainItem : uint32_t { item_continue = 0, item_safe = 1, item_mods = 2, item_settings = 3, main_items = 4 };
// 193: what B knows about A's storage, set by the caller every pass (product: xyf_boot::storage_view).
// unknown: not reported (the offline modes, whose stock OS is embedded: "Continue (built-in OP-XY)");
// waiting: the first 400 ms of A's mount (the first row stays blank: a normal library arrives in about
// 250 ms); starting / checking (a long mount: the full scan after an unclean shutdown) / repairing: the
// first row says "Starting storage...", nothing that needs the library opens; unavailable: the library
// cannot be read this boot (nothing can boot), the first row says so. ready: the library is known.
enum StorageView : uint32_t {
    storage_unknown = 0,
    storage_ready,
    storage_waiting,
    storage_starting,
    storage_checking,
    storage_repairing,
    storage_unavailable
};
// TRACK1..TRACK4 (keys 6..9) jump to the four rows: Continue, the safe-mode row, Mods, Settings. (Keys 10 and 11, once taken for TRACK5/6, are knob pushes.)
struct State {
    Screen screen, info_return; // info_return: screen PLAY goes back to from Info
    uint32_t main_selected; // (the "no firmware" home has one row: no selection)
    bool shift_held;
    const catalog::Catalog* catalog; // may be null (library not loaded)
    uint32_t entry; // the setting for change_setting / pick_setting
    // Set by the caller (xyf_mods_io) every pass. pending: the plan of the saved inputs is not empty (#197; or a
    // toggle flipped is not written yet): PLAY on the first row converges, then boots. continue_label: the first
    // row's text (0: the default's name). continue_note: why the saved selection cannot be built (the first row's
    // status line while it is selected; PLAY still converges and fails with it), 0: none.
    bool pending;
    const char* continue_label;
    const char* continue_note;
    // Set by the caller every pass: the safe-mode row's text, 0 while no setup with mods is saved (the row reads
    // "Boot without mods", dimmed, and PLAY says why); safe_restore: it is "Restore Open-XY". PLAY acts at once
    // (no confirmation). undo_offer: the status line offers to undo a content change whose source is missing
    // (usb_service content_line): PLAY on the first row does that.
    const char* safe_label;
    bool safe_restore, undo_offer;
    // The Settings page: its row and its line. The Mod memory row (xyf_settings.hpp Memory) and its
    // pending mark, set by the caller (xyf_mods_io menu_state) every pass.
    uint32_t settings_selected;
    const char* settings_note;
    uint32_t memory;
    bool memory_pending;
#if defined(XYF_SETTING_OPTIONS)
    // #21: the settings.txt rows' values, set by the caller every pass (usb_service settings_view; xyf_settings.hpp
    // power_on_text, usb_text, confirm_text, saver_text); 0: blank. Texts, not values: the offline stage-2 modes have
    // no settings.txt and no room for its texts. (A newer record's line is settings_note, set there too.)
    const char *power_on_text, *usb_text, *confirm_text, *saver_text;
    // #21: rows marked (a bit per xyf_settings.hpp Row; the value in accent): a settings.txt row changed and not saved
    // yet (#191: saved on its own at a quiet moment), Mod memory likewise (memory_pending). Set by the caller.
    uint32_t settings_marked;
    // #21: the option list (Screen::options; open_options): the setting's option texts, how many, the one in effect
    // (marked "current") and the selected one. The heading is the label of the Settings row (settings_selected).
    const char* const* options;
    uint32_t option_count, option_current, option_selected;
    // Content packs: one page of installed or inbox folder IDs, and the current transaction.
    const char* const* pack_names;
    const char* pack_note;
    const char* pack_result;
    const uint8_t* pack_kinds;
    uint32_t pack_page, pack_count, pack_selected;
    bool pack_ready, pack_more, pack_inbox, pack_confirm, pack_execute, pack_busy;
    bool pack_importing, pack_cancel;

    bool confirm_accept; // which button the pack dialog highlights
#endif
    // 193: StorageView (the caller's, after start() and every pass) and the line a refused row left (0: none).
    uint32_t storage;
    const char* note;
};
// Holding + or - moves through lists. Confirm dialogs take one press per choice.
inline bool repeat_keys(const State& s) {
#if defined(XYF_SETTING_OPTIONS)
    return s.screen != Screen::uninstall && !(s.screen == Screen::packs && s.pack_confirm);
#else
    (void)s;
    return true;
#endif
}
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
// The main menu's line for a refused row (State::note) while it applies, else continue_note while the first row is
// selected, else 0.
const char* main_note(const State& state);
#if defined(XYF_SETTING_OPTIONS)
// #21: the caller's answer to change_setting: the setting's options (texts[current] in effect, selected first).
static const uint32_t max_options = 8, packs_per_page = 7;
inline void open_options(State& s, const char* const* texts, uint32_t count, uint32_t current) {
    s.options = texts;
    s.option_count = count < max_options ? count : max_options;
    s.option_current = s.option_selected = current < s.option_count ? current : 0;
    s.screen = Screen::options;
}
#endif
}}
#endif
