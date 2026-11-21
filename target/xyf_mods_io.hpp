#ifndef XYF_MODS_IO_HPP
#define XYF_MODS_IO_HPP
#include <stdint.h>
#include "xyf_mods.hpp"
#include "xyf_menu.hpp"
// The Mods page's work on processor B : reads the newest complete mod package generation
// under /yaffs2/xy-forever/mods/ through B's timed file client (read-only; one bounded request per step,
// steps for at most slice_ms per menu-loop pass, so USB and the screen stay live), and starts, follows
// and cancels the job through the USB service's own mods.apply start (usb_service mods_start). The page
// writes nothing itself: every write is a job's (the build under tmp/job-<id>/, then its commit into the library;
// the saved selection, config.txt, by the config job).
// The product build only (one link session and the job); elsewhere the menu says "not available".
#if defined(XYF_ONE_SESSION) && defined(XYF_USB_FILE_WRITE)
#define XYF_MODS_PAGE 1
#endif
namespace xy_forever { namespace mods_io {
static const uint32_t slice_ms = 8, redraw_ms = 150, progress_ms = 200, quiet_ms = 1000, read_bytes = 2048;
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
// A key while the page is shown (xyf::mods::key). #197: a flip (a toggle; memory_flip; unknown bases) is saved at
// once: config.txt (mods=, the inputs beside it; what is built stays) is written by the config job at a quiet moment,
// quiet_ms after the last flip, so the menu never waits and quick flips make one write (pump). saving: that write runs.
xyf::mods::Effect key(uint32_t key, bool pressed);
bool saving();
// One slice of the page's work, every menu-loop pass (shown: the page is on screen; when it is not, an
// open folder is closed and the reading stops; a running job is still followed). Nonzero: draw again
// (1: the page changed; 2: only the building bar and its numbers moved, every progress_ms while a job runs).
uint32_t pump(bool shown);
// The longest pump pass this boot (ms).
uint32_t longest_ms();
// The saved configuration (xyf_config.hpp; read once the menu runs) and the converge sequence (#197,
// xyf_converge.hpp). menu_state: the menu's view of it (pending: the plan of the saved inputs is not empty, or a flip
// is not written yet; continue_note: why the selection cannot be built; continue_label with "(rebuild)" or "(to apply)"
// from that plan; safe_label, safe_restore; memory and memory_pending, the Mod memory row); true when the home row's
// text, the safe-mode row or the Mod memory row changed.
// converge_begin(why) converges to the saved inputs and boots (a flip not written yet is written first): why_apply,
// as they are (PLAY on the first row with "(to apply)" or "(rebuild)"); why_safe, "Boot without mods" (safe=1: the
// base with its official content, the selection kept); why_restore, "Restore Open-XY" (safe=0: the saved setup back,
// built again when the slot no longer holds it); why_power_on, as they are (a plain power-on A doubted: xyf_boot
// take_converge). Only the operations the plan needs run, on the progress screen (progress(); converging() while it
// is up), never on the Mods page: its list, its toggles and the home row stay. Each success ends by storing the
// fingerprint (xyf_converge.hpp mark_path) when the stored one differs.
// progress_key: a key there (STOP cancels a build). converge_state: converge_running, then converge_boot (boot the
// default: the desired state) or converge_failed (converge_reason; the default unchanged); converge_done: taken.
enum Why : uint32_t { why_apply = 0, why_safe, why_restore, why_power_on };
enum ConvergeState : uint32_t { converge_idle = 0, converge_running, converge_boot, converge_failed };
bool menu_state(xyf::menu::State& app);
void converge_begin(uint32_t why);
bool converging();
const xyf::mods::Progress& progress();
xyf::mods::Effect progress_key(uint32_t key, bool pressed);
uint32_t converge_state();
const char* converge_reason();
void converge_done();
// #26, usb_service's config.get and config.set: the saved inputs over USB. config_view reads the record fresh (s_ok;
// s_busy while the sequence runs or a job holds the storage; else the read's status): record (1: saved), count, flags
// (bit 0 the 4 MiB layout, bit 1 safe mode, bit 2 an unknown base allowed), saving (1: a flip waits for its quiet
// moment or is written; 2: its write failed), base, generation, the mods= line (mods, mods_bytes; in the page's buffer
// until the next pass). config_set: a flip on the page with the toggles, Mod memory and safe mode given (flags as
// config_view's; mods a comma list of the record's mods= names). It reads the package list again first (s_busy until
// it is, for the request whose CRC-32 is crc: the same request again), checks the selection can be built (s_refused,
// reason as the page says it) and sets it (s_ok): written at the next quiet moment (pick_step). Never a build.
struct ConfigView {
    uint32_t record, count, flags, saving, mods_bytes;
    const char* base;
    const char* generation;
    const char* mods;
};
uint32_t config_view(ConfigView& v);
uint32_t config_set(uint32_t flags, const char* mods, uint32_t crc, const char*& reason);
#endif
} }
#endif
