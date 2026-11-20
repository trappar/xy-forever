#ifndef XYF_USB_SERVICE_HPP
#define XYF_USB_SERVICE_HPP
#include <stdint.h>
#include "usb_proto.hpp"
#include "cold_storage.hpp"
#include "xyf_settings.hpp"
namespace xyf { namespace menu { struct State; } }   // xyf_menu.hpp (settings_view)
// XY Forever USB service (processor b; XYF_USB_SERVICE). Lets the
// host tool (xyf_host.py) read status/files, stage a build, install it into
// the NAND boot partition and reboot, without the dev menu or key presses.
//
// Entry (boot model v3, Test 065): the menu is home. b's file boot opens the menu
// with USB after our own REBOOT (marker), with PLAY at power-on, or when the default
// can't boot; menu_start_usb restarts the stock smxUSBD with the CDC "Serial"
// function only (88a35044 mask 0x400) and the menu loop answers every command
// (home_pump). menu_release() stops USB (88a34fec(1)) before any boot.
//
// Safety (docs/SAFETY.md):
//  - every stock function reached is CRC-checked first (usb_spans.hpp);
//  - INSTALL accepts only a staged image whose SHA-256 the host announced
//    (PUT_BEGIN) AND confirms again (INSTALL token), that parses as an LDR
//    stream, carries the XY Forever cold descriptor and fits the boot
//    partition; it writes only through the stock commit-last A/B writer
//    88a06a7e, then reads the active boot partition back sector by sector;
//  - a answers only the exact Track-5 request set (storage_server.hpp);
//  - never SPI flash, companions or SPI metadata.
namespace xy_forever { namespace usb_service {
#if defined(XYF_SIM)
uint32_t idle_gc_state(); // simulator-only: active/open-file flags and reference count
#endif

static const uint32_t magic = 0x31425355u;          // "USB1"
// b SDRAM (hold-mode b never uses its session scratch 0x8b800000..0x8b810000
// except the remote-read tail): frame buffers, file buffer, read-back buffer.
static const uint32_t rx_frame = 0x8b800000u, tx_frame = 0x8b802000u, file_buffer = 0x8b804000u;
static const uint32_t rx_chunk = 0x8b80c000u, rx_chunk_bytes = 0x800u;   // CDC reads (Test 038: was 256 B on the stack)
// Test 038: a 4 KiB READ_FILE reply hung b (the 1.6 KB STATUS reply never
// has); ranged reads return at most 1 KiB until that is understood.
static const uint32_t read_range_max = 1024;
// SCREEN (Test 053): read-only view of b's display framebuffer (pointer at the
// stock word 0x8803dc58, as key_ui; layout in ui_gfx.hpp: 480 columns stored
// right-to-left, 256 RGB565 pixels each). Only inside [fb, fb + screen_bytes).
static const uint32_t screen_pointer = 0x8803dc58u, screen_bytes = 0x3c000u;
// PSoC power status (stock 88a1c8fc reads it; 88a1cc38 stores it): 0x16 = USB power.
// Recorded only: it read 0x10 on charging-path boots and fast power cycles alike
// (Test 039 reports 031-033 / 038), so it cannot detect USB.
static const uint32_t power_status_address = 0x88a1cdb4u, power_status_usb = 0x16;
// Reboot marker (Test 040): REBOOT writes {magic, ~magic, count} here and flushes
// it before the processor reset; SDRAM survives the reset (not a power-off). The
// next cold entry sees it, clears it and runs the service even though the stock
// bootloader did not start USB. Above the staging/read-back buffers; no LDR
// record of an XY Forever build or the stock OS loads it.
static const uint32_t reboot_marker = 0x8bfff000u, reboot_magic = 0x52465859u;   // "YXFR"
// Marker words: magic, ~magic, reason, crash restarts. Reason active = the service
// was running (set once it starts, cleared on every clean exit): a reset of b
// therefore comes back into the service, at most max_crash_restarts times
// in a row, then the boot is normal.
static const uint32_t marker_reboot = 1, marker_active = 2, max_crash_restarts = 3;
// Test 057's USB probe (b started USB itself and waited probe_ms for the stock
// host bus-reset flag at host_flag/host_stamp) was removed after Test 060; its
// constants stay for report decoding. present = 4 meant "probe found a host".
static const uint32_t probe_ms = 1500, probe_present = 4;
static const uint32_t play_present = 5;   // Test 063: service entered with PLAY at power-on
static const uint32_t host_flag = 0x88a35929u, host_stamp = 0x88a3592cu;
// HOLD (Test 041; v3: home_pump): payload u32 1 keeps home (the menu), 0 leaves it
// to the menu's own rules; the marker reason flag carries it across REBOOT.
static const uint32_t marker_held = 0x100u;
// Reason bit 0x200 (the removed watchdog guard) is retired: never set, ignored.
// Test 064 (project rule): REBOOT payload u32 target: 0 (or none) = back into the
// USB service (as before), 1 = straight to the OS (no marker), 2 = the boot menu
// with USB (marker_menu; like PLAY at power-on). Also from the menu over USB.
static const uint32_t marker_menu = 0x400u;
static const uint32_t reboot_service = 0, reboot_os = 1, reboot_menu = 2;
// SET_DEFAULT (Test 048): b waits up to this long for a's state write to finish.
static const uint32_t write_patience_ms = 60000;
static const uint32_t image = 0x8b820000u, image_capacity = 0x700000u;   // staging (b)
static const uint32_t readback_buffer = 0x8bf20000u, readback_sectors = 128;   // 64 KiB, end of staging
// Install log (Test 035, version 3): while the stock writer runs, b's stock log
// level [0x11b00074] is raised to 3 and the four stock log sinks (0x8803d06c,
// muted by the peer session) hold only log_sink; each line goes to the host as
// an unsolicited t_log frame (seq 0) built in log_frame. Restored before any
// session step (poll() requires muted sinks). Never sent from an interrupt
// (IPEND) and never after the first line the host did not take.
static const uint32_t log_frame = 0x8b80e000u, log_text_max = 200;
static const uint32_t log_level_address = 0x11b00074u, log_sinks = 0x8803d06cu, log_level = 3;
static const uint32_t log_time_bytes = 4;          // 11a01d96 line prefix (binary)
static const uint32_t descriptor_magic = 0x584c4348u;  // cold descriptor "HCLX" (xyf_build.py)
enum Exit : uint32_t {
    exit_none = 0, exit_not_present, exit_identity, exit_not_configured, exit_no_hello, exit_continue,
    exit_idle, exit_cap, exit_reboot, exit_link
};
struct Result {
    uint32_t magic, version, role, present;
    uint32_t identity, exit, configured_ms, hello_at_ms;
    uint32_t frames, bad_crc, dropped, replies;
    uint32_t write_errors, reads, read_bytes, last_type;
    uint32_t last_status, put_size, put_received, put_crc;
    uint32_t put_ok, install_status, install_written, install_ms;
    uint32_t verify_partition, verify_sectors, verify_mismatch, capacity;
    uint32_t heap_before, heap_after, start_cycles, end_ms;
    // a (role 0): install-mode counters (storage_server serve_install); a_selected: the confirmed
    // partition | 1 << 8 while a boot-partition select was attempted
    uint32_t a_identity, a_install_enabled, a_selects, a_trims;
    uint32_t a_reads, a_writes, a_refused, a_selected;
    uint32_t a_last_refused, a_reboot, stack_used, a_state_write;   // status | result << 8 | step << 16 | seq << 24
    // Version 2 (Test 031 found 0 bytes received after the host opened the
    // port): CDC receive counters and read-only USB snapshots (diag_layout).
    uint32_t rx_events, rx_bytes, diag_count, stock_callback;   // CDC receive callback found at start (cleared while serving)
    uint32_t stage2[8];                     // stage-2 load record (s2w_* words below; was diag[0][0..7])
    // Version 5 (#107), in the first unused diag words: the build identity (usb_service_identity.ipp).
    // release: XYF_VERSION_CODE; image: the first 4 bytes of the running image's SHA-256 (hello image=);
    // image_state: ImageState (0 not started: no menu this boot); image_ms: menu start to known (ms);
    // image_kcycles: the hashing's own time, CYCLES / 1024.
    uint32_t release, image, image_state, image_ms, image_kcycles;
    uint32_t diag[59];                      // unused since Test 041 (diag_count 0)
    // Version 3 (Test 034 INSTALL gave no reply): install log forwarding.
    uint32_t log_lines, log_sent, log_skipped, log_level_before;
    uint32_t log_sink_before, install_step, install_step_ms, session_cap;
    // Version 4 (Test 038: REBOOT / fast power cycle skipped the service):
    // present = 1 stock USB running, 2 USB power only (service started anyway).
    uint32_t power_status, boot_reason, crash_restarts, retired_kicks;   // present: presence() | held << 4; retired_kicks: always 0 (was the removed guard's count)
};
static_assert(sizeof(Result) == 132 * 4, "Result layout");
// stage2[] (usb_service.cpp stage2_load): words, and the status word's values. detail: the
// file client's status (missing, link, read), the xyf::stage2::Check (check), or stage 2's answer
// (init, hook). reads: stage-2 file reads this boot (a plain boot: none).
enum Stage2Word : uint32_t {
    s2w_status = 0, s2w_detail, s2w_version, s2w_bytes, s2w_load_ms, s2w_trace_code, s2w_trace_value, s2w_reads
};
enum Stage2Status : uint32_t {
    s2_none = 0, s2_loaded, s2_missing, s2_link, s2_read, s2_check, s2_copy, s2_init, s2_hook
};
// install_step: last step INSTALL reached (also sent as a log line).
enum InstallStep : uint32_t {
    step_none = 0, step_checked, step_a_install, step_capacity, step_writer, step_writer_done, step_verify_done
};
// One snapshot (all reads are side-effect free: no MUSB interrupt-status or
// FIFO registers, which clear or pop on read):
//  [0] ms since start   [1] SEC SCTL 0x4d  [2] SEC SSTAT 0x4d  [3] SCTL 0x4e  [4] SSTAT 0x4e
//  [5] FRAME | POWER << 16 | FADDR << 24   [6] INTRTXE | INTRRXE << 16
//  [7] INTRUSBE | DEVCTL << 8 | INDEX << 16
//  [8 + 2k] EP(k+1) RXCSR | RXCOUNT << 16, [9 + 2k] TXCSR | RXMAXP << 16 (k = 0..2)
//  [14 + j] DMA channel 2j CTL | channel 2j+1 CTL << 16 (j = 0..3)
//  [18] CDC port 0 pointer [0x880734bc]  [19] port +4 (line state open)
//  [20] RX ring pointer (port +0x18)  [21..23] ring +0, +8, +0xc
extern Result result;
// Both roles: CRC of every span in usb_spans.hpp (bit i set = span i differs,
// folded into 32 bits). 0 = identical to the authenticated bootloader.
uint32_t identity();
#if defined(__bfin__)
uint32_t presence();     // 0 none, 1 stock USB running, 3 after an XY Forever REBOOT
// Role 0: set the library default to the entry whose id hashes to hash (the
// only write; xyf::state_write + cold_storage write_state_slot). Records
// result.a_state_write. Uses the file server's read-only mount (one per boot).
#if defined(XYF_USB_SET_DEFAULT)
void a_set_default(cold_storage::Stock121ReadOnlySession& storage, uint32_t hash);
#endif
// Dry-run boot (Test 045, product build): CONTINUE with payload u32 1. b tells
// a (dry_run_item); both run the whole file/library boot (scan, transfer,
// admit, commit, hand-off copy) and then, instead of starting the OS, b saves
// its file_boot result at dry_run_area {magic, bytes, block} and both reset;
// b's reboot marker brings the service back and STATUS returns the block.
// No OS runs and nothing is written to storage.
static const uint32_t dry_run_item = 9, dry_run_area = 0x8bffe000u, dry_run_magic = 0x59524458u;   // "XDRY"
static const uint32_t dry_run_max_bytes = 0x800u;
bool dry_run_requested();
void a_dry_run();        // role 0: b sent dry_run_item
// Saves block (b only), sets b's reboot marker, resets. Never returns.
void dry_run_finish(const void* block, uint32_t bytes);
// Test 058 (b): the menu has USB (menu_start_usb); menu_release() stops USB before any boot.
bool menu_requested();
void menu_release();
// Test 062 (b): progress lines on the service screen (drawn only if a host said HELLO this boot).
void progress(const char* a, const char* b);
#if defined(XYF_USB_SERVICE)
void stack_watch(uint32_t painted_begin);   // b's painted stack floor; STATUS reports stack_used live
#else
inline void stack_watch(uint32_t) {}        // no USB status in builds without the service
#endif
#if defined(XYF_ONE_SESSION)
// Test 065 (b): one link session; the menu is home and answers every USB command.
void rpc_reset();
bool home_active();
bool home_hold();
bool home_begin();
void home_start();
uint32_t home_pump();
// #144: the menu loop's pause after a pass (us): 5 ms, shorter while MTP traffic is active. drawn(start): a redraw
// that began at start (cycles), counted in MTP's timing while that traffic is active.
uint32_t menu_pause_us();
void drawn(uint64_t start);
// b: the menu has USB on. A lost link then keeps the menu: USB still answers (commands
// that need a get link) until a power cycle; b never resets itself on it.
bool home_usb();
// b: what the menu shows of the host (key_ui draw_screen). host_seen: a valid frame arrived within
// host_seen_ms (the USB mark: steady under scripts/keepalive.sh, one status call about every 2.5 s, and out
// within 5 s of the host's last frame). host_activity: while a job started over USB runs ("USB: building
// Open-XY", "USB: changing content"), or within host_activity_ms of a request that changes something
// ("USB: receiving a file", "USB: writing files", "USB: installing XY Forever", ...), that line; else null.
// version_label: "v" and the release version HELLO reports (XYF_VERSION), the home page's corner.
// image_label: "image " and the image HELLO reports (8 hex digits; "..." until known; "unknown"); another
// pointer once the hash settles (image_step, about 0.5 s after the menu starts), so a drawn Info page can tell.
static const uint32_t host_seen_ms = 5000, host_activity_ms = 2000;
bool host_seen();
#if defined(XYF_MTP_USB)
// The screen's MTP mark: the composite is up and a transfer runs or an MTP command arrived within mtp_seen_ms (5 s).
bool mtp_seen();
#endif
// 188: the marks the screen shows: bit 0 host_seen (USB), bit 1 mtp_seen (MTP; 0 without the composite).
uint32_t host_marks();
const char* host_activity();
const char* version_label();
const char* image_label();
#if defined(XYF_USB_FILE_WRITE)
// b: the menu's Mods page (xyf_mods_io.cpp). mods_start: the start mods.apply runs (the same
// request bytes, SHA-256 included; status, job id, missing input; missing = mods_ids_taken with
// status state when both mods ids are the default and the boot-once entry). job_view: the running or last
// job as job.status sees it (state 0 none, 1 running, 2 ended; the library commit is phase finish,
// commit = 1 while it runs, done of 3; a failed commit is failed). job_stop: job.cancel's stop (s_ok; s_state
// in the commit, which cannot be cancelled; s_not_found: no job runs; a content switch is shown with
// its own id, commit 0, and job_stop never stops it: s_not_found). page_serving: is A XY Forever's file
// engine (s_ok, else s_link; one request). page_mount: B's op-8 state, for the page's reads. page_busy:
// a job owns the slot (mods.apply or a content switch) or its write window is closing: the page
// sends no storage request then (no request; A's handles are gone after the window).
static const uint32_t mods_ids_taken = 5;
struct JobView { uint32_t state, id, phase, done, total, error, status, debris, commit; };
uint32_t mods_start(const uint8_t* payload, uint32_t length, uint32_t& job, uint32_t& missing);
void job_view(JobView& view);
uint32_t job_stop();
uint32_t page_serving();
volatile uint32_t& page_mount();
bool page_busy();
// the menu's Continue, Discard, Boot once and Set default while a job owns the write
// window: this line instead of the action (null: none runs; injected keys see it too).
inline const char* menu_busy() { return page_busy() ? "Busy: a build or content change is running." : nullptr; }
#endif
#if defined(XYF_USB_FILE_WRITE) && defined(XYF_USB_SET_DEFAULT) && defined(XYF_ONE_SESSION)
// b: the content switch (xyf_content_job.hpp), a job of B's menu loop. content_default: the menu's
// SHIFT+PLAY on catalog entry `entry`: 0 when its content is installed (set the default as before), else the
// status line (the switch started; it writes the default when it succeeded; or why it did not start).
// content_line: the menu's status line while a switch runs and once it ended; 0 when unchanged.
#define XYF_CONTENT_SWITCH 1
const char* content_default(uint32_t entry);
const char* content_line();
// b: the save sequence's storage steps (xyf_mods_io; xyf_config.hpp). content_switch: the switch to
// library entry id as a job, without writing the default (status as content_default's start: s_ok started,
// s_busy, s_link, s_bad_request). config_store: the config job (job_view: phase 0 running, 8 done, 9 failed;
// error as a switch's): the record written (replaced by renames), A's window closed, then the default set to
// default_id unless it is that already ("" or 0: not changed). job_target: the entry the last mod job
// committed into ("mods-a" / "mods-b").
uint32_t content_switch(const char* id);
// The save-time log (xyf_save_log.hpp). save_log_begin: the Save key (the times start; the build, the
// switch and the record add theirs; the record's job writes the line); save_log_selection: what was asked;
// save_log_lap: the part from now (xyf::save_log::Part); save_log_drop: the Save failed (no line).
void save_log_begin();
void save_log_selection(uint32_t mods, bool expanded, uint32_t added, uint32_t removed);
void save_log_lap(uint32_t part);
void save_log_drop();
uint32_t config_store(const char* text, uint32_t length, const char* default_id);
const char* job_target();
// settings.txt (xyf_settings.hpp). settings_load: once A serves, one bounded read (settings.txt, else
// settings.old; absent or unreadable: the defaults); true when done (call each menu pass until then).
// settings: the record (the defaults until loaded). settings_store: the record replaced as config.txt is (the
// config job with settings' paths, no default change; status as config_store).
#define XYF_SETTINGS 1
bool settings_load();
xyf::settings::Record& settings();
uint32_t settings_store();
// #21: PLAY on a settings.txt row (xyf_settings.hpp Row: Menu at power-on, USB mode; 0 for any other): the record
// changed and its write started (settings_store), or not with the reason; the page's note. A newer record (newer)
// is never changed. settings_view, each menu pass: settings_load, the
// rows' values into the menu state, and once the write ended its note (failed: read again); true: draw again.
const char* settings_change(uint32_t row);
bool settings_view(xyf::menu::State& state);
// A key event (key_ui on_key; injected: the host's KEY command). While a confirm prompt is open it takes
// every key (true: not for the menu); only a real PLAY / STOP answers it (xyf_settings.hpp gate_key).
bool confirm_key(uint32_t key, uint32_t pressed, bool injected);
#endif
#endif
#if defined(XYF_SERVICE_ON_MARKER_ONLY)
// Test 060: the menu starts USB itself.
void menu_start_usb();
// 097: why menu_start_usb left USB off (stock code check failed), or 0.
const char* usb_notice();
#endif
// Both roles: stock processor reset (RCU), never returns.
void reset_self();
#endif
// Pure helpers (host-tested).
// Finds the cold descriptor magic followed by version 1 (4-byte aligned).
bool has_descriptor(const uint8_t* data, uint32_t size);
// UNINSTALL: the stock 1.2.1 os.ldr (the dev-menu restore image,
// private/xy-forever/recovery/stock-1.2.1/os.ldr), identified only by
// its exact size and SHA-256; the image itself is never embedded. It is
// written exactly as dev-menu Track 5 writes it (88a1f24a: 88a06a7e(buf,
// size, 0)). The real 1.2.1 SPI bootloader is not touched either way.
static const uint32_t stock_os_bytes = 2373336u;
static const uint8_t stock_os_sha256[32] = {
    0x60, 0x35, 0x8a, 0xfd, 0xaa, 0x9b, 0x58, 0xed, 0x40, 0x41, 0x16, 0x02, 0xf6, 0xd5, 0xd4, 0xe1,
    0xe3, 0xe0, 0x25, 0x53, 0xff, 0x2d, 0xa7, 0xe2, 0x50, 0x38, 0xe7, 0x1d, 0xd0, 0x02, 0xb5, 0x2f};
// Image acceptance before anything is written. INSTALL: the XY Forever cold
// descriptor must be present (unchanged). UNINSTALL: size and SHA-256 (of the
// staged, PUT_END-verified image) must equal the stock constants exactly; no
// other descriptor-less image is ever accepted. Both then pass the native LDR
// structure check on the device.
bool accept_image(bool uninstall, bool descriptor, uint32_t size, const uint8_t* sha256);
// Boot partition sector-0 header written by 88a06a7e: version >= 2,
// logical bytes, CRC-32 of the logical bytes, valid byte, encrypted byte.
// Encrypted is 0 or 1: with the OTP key present the writer encrypts the source
// buffer IN PLACE (11a02170(buf, buf, ...)) and sets it, so the staged image
// is ciphertext afterwards (read-back compares ciphertext; no second INSTALL
// of the same staging).
bool header_matches(const uint8_t* sector, uint32_t size, uint32_t crc);
} }
#endif
