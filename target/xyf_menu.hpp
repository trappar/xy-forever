#ifndef XYF_MENU_HPP
#define XYF_MENU_HPP
#include <stdint.h>
#include "xyf_catalog.hpp"
// XY Forever menu logic (processor b): a pure state machine. Keys in, an
// action out, and a model the UI pages draw. No stock calls, no I/O.
namespace xyf { namespace menu {
// Key enum indices from the stock bootloader table (KLeft/KRight are the
// physical "-" / "+" keys). Stop = back on every screen. 8.2: a push of any of the four knobs
// (KEncClick1..4 = key_knob1..+3) is PLAY everywhere (SHIFT + push = SHIFT + PLAY); the turns are not keys here.
enum Key : uint32_t { key_minus = 53, key_plus = 54, key_play = 51, key_stop = 52, key_shift = 55, key_track1 = 6,
                      key_knob1 = 10, knobs = 4, tracks = 4 };
// A knob push as PLAY; any other key unchanged.
inline uint32_t as_play(uint32_t k) { return k - key_knob1 < knobs ? uint32_t(key_play) : k; }
// 8.6: or'd into - / + for a knob turn's row (key_ui turn_knobs; the Mods page only: SHIFT + turn = its filter).
static const uint32_t key_turn = 0x100u;
enum class Screen : uint32_t { main, library, info, confirm_uninstall, home_empty, confirm_save, settings };
enum class Action : uint32_t {
    none,
    redraw,          // state changed: draw again
    continue_boot,   // boot the default (or the built-in copy)
    boot_once,       // boot library entry `entry` once
    set_default,     // make library entry `entry` the default (needs a write)
    uninstall,       // confirmed: put back stock os.ldr, delete xy-forever/
    open_mods,       // 5.4e1: open the Mods page (xyf_mods; stage 1 runs it)
    save,            // 8.4a: the Save dialog's Save: apply the pending configuration, then boot it
    discard,         // 8.4a: the Save dialog's Discard: forget the pending changes, boot the saved configuration
    change_setting   // 4.8: the Settings page's PLAY on setting `entry` (xyf_settings.hpp Row; 4.8m2: Mod memory, pending)
};
// 8.4a: the Save dialog's buttons.
enum SaveChoice : uint32_t { choice_save = 0, choice_discard = 1, choice_back = 2 };
// Main menu rows, in order (5.4e1b: Mods right below Firmware library).
// 188: no MTP mode row (MTP is always on with the composite USB device).
enum MainItem : uint32_t { item_continue = 0, item_library = 1, item_mods = 2, item_uninstall = 3,
                           item_settings = 4, item_info = 5, main_items = 6 };   // 4.8: Settings above Info
// 193: what B knows about A's storage, set by the caller every pass (product: xyf_boot::storage_view).
// unknown: not reported (the offline modes, whose stock OS is embedded: "Continue (built-in OP-XY)");
// waiting: the first 400 ms of A's mount (the first row stays blank: a normal library arrives in about
// 250 ms); starting / checking (a long mount: the full scan after an unclean shutdown) / repairing: the
// first row says "Starting storage...", nothing that needs the library opens; unavailable: the library
// cannot be read this boot (nothing can boot), the first row says so. ready: the library is known.
enum StorageView : uint32_t { storage_unknown = 0, storage_ready, storage_waiting, storage_starting, storage_checking,
                              storage_repairing, storage_unavailable };
// TRACK1..TRACK4 (keys 6..9) jump to the first four rows: Continue, Firmware library, Mods, Uninstall. (8.2: keys 10 and 11, once taken for TRACK5/6, are knob pushes.)
struct State {
    Screen screen, info_return;       // info_return: screen PLAY goes back to from Info
    uint32_t main_selected, library_selected, home_selected;
    bool confirm_accept;              // which button the confirm dialog highlights
    bool shift_held;
    const catalog::Catalog* catalog;  // may be null (library not loaded)
    uint32_t entry;                   // entry for boot_once / set_default
    // 8.4a, set by the caller (xyf_mods_io) every pass: changes are pending (Continue asks to save them first);
    // save_blocked: they cannot be saved (the dialog offers Discard / Back and says save_note); the first
    // row's text (0: the default's name); in the library, the catalog entry shown as "Open-XY" and the spare
    // mods entry not shown (~0u: none).
    uint32_t save_selected;
    bool pending, save_blocked;
    const char* continue_label;
    const char* save_note;
    uint32_t open_xy, hidden;
    // 4.8: the Settings page: its row and its line. 4.8m2: the Mod memory row (xyf_settings.hpp Memory) and its
    // pending mark, set by the caller (xyf_mods_io menu_state) every pass.
    uint32_t settings_selected;
    const char* settings_note;
    uint32_t memory;
    bool memory_pending;
    // 193: StorageView (the caller's, after start() and every pass) and the line a refused row left (0: none).
    uint32_t storage;
    const char* note;
};
// 193: the row needs the library (shown dimmed, PLAY refused while storage is not ready).
bool needs_storage(const State& state, uint32_t item);
// Start screen: the main menu, or the "no firmware" home when no catalog
// entry is usable and there is nothing built in to continue to.
void start(State& state, const catalog::Catalog* catalog, bool has_builtin);
// One key event (pressed = 1 on press, 0 on release). Returns the action.
Action key(State& state, uint32_t key, bool pressed);
// Label of a main-menu row (e.g. "Continue: OP-XY 1.2.1").
const char* main_label(const State& state, uint32_t item);
} }
#endif
