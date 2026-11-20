#ifndef XYF_MODS_IO_HPP
#define XYF_MODS_IO_HPP
#include <stdint.h>
#include "xyf_mods.hpp"
#include "xyf_menu.hpp"
// The Mods page's work on processor B : reads the newest complete mod package generation
// under /yaffs2/xy-forever/mods/ through B's timed file client (read-only; one bounded request per step,
// steps for at most slice_ms per menu-loop pass, so USB and the screen stay live), and starts, follows
// and cancels the job through the USB service's own mods.apply start (usb_service mods_start). The page
// writes nothing itself: every write is the job's (under tmp/job-<id>/, then its commit into the library).
// The product build only (one link session and the job); elsewhere the menu says "not available".
#if defined(XYF_ONE_SESSION) && defined(XYF_USB_FILE_WRITE)
#define XYF_MODS_PAGE 1
#endif
namespace xy_forever { namespace mods_io {
static const uint32_t slice_ms = 8, redraw_ms = 150, progress_ms = 200, read_bytes = 2048;
static const char root[] = "/yaffs2/xy-forever/mods";
#if defined(__bfin__) && defined(XYF_MODS_PAGE)
// The page (kept for the whole boot: the toggles stay across visits).
xyf::mods::Page& page();
// The menu opened the page: reads the packages unless a complete list is already held.
void open();
// The Settings page is shown (every pass): the reading starts as open() does when it is not running or
// done (pump(true) then carries it; its base says whether Mod memory can be the 4 MiB layout). memory_flip: a pick
// of the other option in Mod memory's list (#21; or PLAY on the row while it cannot change): the pending choice
// flipped, or the line saying why not (xyf::mods::flip_memory).
void prepare();
const char* memory_flip();
// A key while the page is shown (xyf::mods::key).
xyf::mods::Effect key(uint32_t key, bool pressed);
// One slice of the page's work, every menu-loop pass (shown: the page is on screen; when it is not, an
// open folder is closed and the reading stops; a running job is still followed). Nonzero: draw again
// (1: the page changed; 2: only the building bar and its numbers moved, every progress_ms while a job runs).
uint32_t pump(bool shown);
// The longest pump pass this boot (ms).
uint32_t longest_ms();
// The saved configuration (xyf_config.hpp; read once the menu runs) and the save sequence. menu_state:
// the menu's view of it (pending, save_blocked, save_note, continue_label, safe_label, safe_restore; memory and
// memory_pending, the Mod memory row); true when the home row's text, the safe-mode row or the Mod memory row
// changed. save_begin: the Save dialog's Save (the page then shows the sequence's progress;
// STOP cancels while the build runs); save_state: save_running, then save_boot (the default is the saved
// configuration: boot it) or save_failed (save_reason; the changes stay pending); save_done: taken.
// safe_begin(false): "Boot without mods" (content to the base, the default the base, the record kept with
// safe=1); safe_begin(true): "Restore Open-XY" (content to the saved slot, the default the slot, safe=0; it fails,
// building nothing, when the slot no longer holds what built= says). Both run from the home menu: the Mods page is
// never shown nor changed (its toggles stay). safe_line: their progress for the home menu's status line when it
// changed (0: unchanged). Both end as a save does: save_boot, or save_failed.
enum SaveState : uint32_t { save_idle = 0, save_running, save_boot, save_failed };
bool menu_state(xyf::menu::State& app);
void save_begin();
void safe_begin(bool restore);
const char* safe_line();
uint32_t save_state();
const char* save_reason();
void save_done();
#endif
} }
#endif
