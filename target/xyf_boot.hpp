#ifndef XYF_BOOT_HPP
#define XYF_BOOT_HPP
#include <stdint.h>
#include "xyf_catalog.hpp"
namespace xy_forever { namespace peer_services { class Stock121PeerSession; } }
// XY Forever menu boot (Test030, on top of library boot):
// one staged link session per boot (Stock121PeerSession::stage).
//  - a (role 0): opens the session, then mounts read-only, scans the library
//    and encodes the catalog. Commands from b (tagged op-10 arguments):
//      CATALOG       -> serve the catalog as a blob (session stays held)
//      BOOT_DEFAULT  -> load the default entry; BOOT(i) -> load entry i
//    Each command is a request that a's loop answers (Stock121PeerSession::ask),
//    so b may send its first one while a is still mounting.
//    A loaded entry is checked (info.txt size/CRC), the storage is unmounted,
//    the image admitted (LDR policy/entry/slots), hashed, frozen and served;
//    any failure refuses (both boot embedded).
//  - b (role 1): without PLAY -> BOOT_DEFAULT at once. With PLAY -> CATALOG,
//    decode, then the XY Forever menu (key_ui) with the
//    library; Continue -> BOOT_DEFAULT, a library entry -> BOOT(i).
//  - Commit and hand-off exactly as file boot (release after restore).
//  - Set default (menu SHIFT+PLAY on a library entry): b sends
//    xyf::state_write::item_set_default + i; a writes the next A/B state slot
//    through Stock121ReadOnlySession::write_state_slot (the only write path),
//    rescans and re-encodes the catalog; b re-requests the catalog and reports
//    whether its default now is entry i. Never on a normal boot.
// key_ui hooks below run on b inside the menu loop.
namespace xy_forever { namespace xyf_boot {
static const uint32_t item_catalog = 0x10, item_boot_default = 0x11, item_boot_entry = 0x20;   // + index
// Test 059 (XYF_MENU_ON_FAILURE): CHECK the default / entry + index before BOOT.
// a reads, matches and admits the image with storage still mounted and answers
// a CheckStatus blob; a following BOOT of the same index serves it. A failed
// check keeps the session (and the mount) for the menu.
static const uint32_t item_check_default = 0x13, item_check_entry = 0x30;   // + index (0x30..0x3f)
static const uint32_t status_buffer = 0x8b814000u;   // a: in scan scratch (free after the scan); b: below
static const uint32_t b_status_buffer = 0x8b81f000u; // b: received CheckStatus
// Test 063: the peer client needs whole 512-byte sectors (stock padding): a 52-byte
// blob into 0x100 bytes failed client_init (capacity) -> "Storage link lost" (062).
static const uint32_t b_status_capacity = 0x400u;    // to 0x8b81f400, below b's staging 0x8b820000
static const uint32_t check_magic = 0x4b484358u;     // "XCHK"
static const uint32_t check_ms = 20000;              // b: bound for one CHECK (a reads the whole image)
static const uint32_t boot_transfer_ms = 30000;      // b: bound for the image transfer after BOOT (Test 061)
// Offline timing probe: phase durations, not wall-clock estimates. A's CHECK reply carries
// its mount/scan/read/check timings to B; the remaining A phases are available
// only in A's own report. A zero duration is meaningful only when its bit is set.
static const uint32_t timing_magic = 0x31544d42u;   // "BMT1"
enum TimingValid : uint32_t {
    timing_mount = 1, timing_scan = 2, timing_read = 4, timing_crc = 8,
    timing_a_admit = 16, timing_sha = 32, timing_freeze = 64,
    timing_check = 128, timing_receive = 256, timing_b_admit = 512,
    timing_handoff = 1024
};
struct TimingInfo {
    uint32_t magic, version, valid, a_mount_start_ms;
    uint32_t a_mount_ms, a_scan_ms, a_read_ms, a_crc_ms;
    uint32_t a_admit_ms, a_sha_ms, a_freeze_ms, a_ready_ms;
    uint32_t b_check_ms, b_receive_ms, b_admit_ms, b_handoff_ms;
};
static_assert(sizeof(TimingInfo) == 64, "TimingInfo layout");
// B's image transfer in detail (dry-run result version 6), all from BOOT on B's clock.
// 0 means the stage was not reached. metadata: A hashed and froze the image and its
// metadata arrived; data: the last sector batch was requested; last_poll: the poll
// that took the last batch and ran B's CRC and SHA-256; policy: file_boot admit();
// admit_hash: the session's second SHA-256; release: restore/release incl. A's hash.
static const uint32_t timing_detail_magic = 0x31445442u;   // "BTD1"
struct TimingDetail {
    uint32_t magic, version, b_metadata_ms, b_data_ms;
    uint32_t b_last_poll_ms, b_policy_ms, b_admit_hash_ms, b_release_ms;
};
static_assert(sizeof(TimingDetail) == 32, "TimingDetail layout");
struct CheckStatus {
    uint32_t magic, reason, index, load_reason, admit_reason;   // reason: file_boot Reason (0 = bootable)
    char id[32];
    TimingInfo timing;
    uint32_t power_on;      // #21: a check of the default: Menu at power-on (xyf_settings.hpp PowerOn; A's read)
    uint32_t converge;      // #197: a check of the default: why B converges before it boots (xyf_converge.hpp Doubt)
};
static_assert(sizeof(CheckStatus) == 124, "CheckStatus layout");
// b: bound for a's answer to one command. a answers from its own loop, so this covers
// whatever a is busy with: the mount and scan (5.1 s seen once, normally 0.1 s), an
// image read, a hash.
static const uint32_t command_ms = 15000;
// 191 (incident 2026-11-16): A's storage start. A's read-only mount (a full yaffs scan after an
// unclean shutdown: 4.5-17 s seen) may outlast every other bound, and may fail. B asks for the catalog
// without blocking its menu ("Storage: starting..."), waits up to start_ms per attempt (A answers
// after its mount), and while A's mount has failed asks again every retry_ms (A mounts again first).
static const uint32_t start_ms = 300000, retry_ms = 5000;
// A link user (USB file or menu command) waits for a catalog request in flight at most fetch_wait_ms.
static const uint32_t fetch_wait_ms = 1000;
// A's word in the catalog's reserved header field (+12): result (cold_storage::Result; 0xff: mounted
// but the direct reads are gated off) | attempts << 8 | denied media changes << 16 | the last attempt's
// time in 1.024 s units << 24 (each <= 255). 0: an A before 191. Mounted and readable: attempts != 0, result 0.
static const uint32_t storage_unreadable = 0xffu;
inline bool storage_failed(uint32_t word) { return (word >> 8 & 0xffu) && (word & 0xffu); }
// 192 (StorageStart version 2): denied media changes take bits 16-21 (<= 63); bits 22-23 are A's
// storage repair this boot: none, pending (the read-only mount failed with write_attempt; A repairs
// at B's next ask, which B sends at once), done, failed (final: A mounts no more, B stops asking).
// After done or failed the result and time bytes are the repair's (with its read-only mount).
static const uint32_t repair_none = 0, repair_pending = 1, repair_done = 2, repair_failed = 3;
inline uint32_t storage_repair(uint32_t word) { return word >> 22 & 3u; }
// B's view of it, a USB STATUS block. phase: 0 not asked, 1 waiting for A, 2 storage up, 3 A's mount
// failed (B asks again), 4 lost (no answer within start_ms). waited_ms: the current wait (0: none).
static const uint32_t start_magic = 0x31545353u;     // "SST1"
struct StorageStart {
    uint32_t magic, version, phase, asks;
    uint32_t waited_ms, answered_ms, a_word, longest_ms;
};
static_assert(sizeof(StorageStart) == 32, "StorageStart layout");
extern StorageStart storage_start;
// a: before it resets on a command, wait for its answer to leave (at most answer_out_ms;
// one request round trip is a few ms), then stay quiet for answer_quiet_ms.
static const uint32_t answer_out_ms = 50, answer_quiet_ms = 5;
static const uint32_t catalog_buffer = 0x8b810000u, catalog_capacity = 0x1000u;   // both roles
static const uint32_t library_buffer = 0x8b812000u;                                 // a: Library; b: Catalog
static const uint32_t scan_buffer = 0x8b814000u, scan_capacity = 0xa000u;          // a: scan scratch
static const uint32_t blob_ms = 15000;          // b: bound for the catalog transfer
static const uint32_t write_ms = 30000;         // b: bound for set-default (a's write + remount + rescan)
// a: set-default plan and read-back, in staging (free until a boot command).
static const uint32_t plan_buffer = 0x8b820000u, readback_buffer = 0x8b822000u;
#if defined(XYF_ONE_SESSION)
// v3: no session cap (wrap-safe xyf_clock); the menu stays up as long as the user wants.
// A's idle rule still ends a session whose B stopped talking.
static const uint32_t role0_cap_ms = 0xffffffffu;    // = peer_services::no_cap
static const uint32_t role1_menu_cap_ms = 0xffffffffu, role1_boot_cap_ms = 30000;
#else
static const uint32_t role0_cap_ms = 300000;    // a: whole held session (menu); idle rule ends it sooner
static const uint32_t role1_menu_cap_ms = 300000, role1_boot_cap_ms = 30000;
#endif
struct MenuInfo {
    uint32_t play, catalog_bytes, catalog_ok, catalog_entries;
    uint32_t choice, choice_entry, commands, commands_seen;   // choice: 0 none, 1 default, 2 entry
    uint32_t blobs, catalog_ms, menu_ms, boot_ms;
    uint32_t last_command, scan_error, refused, state_write;   // state_write: status | result << 8 | step << 16 | seq << 24
};
static_assert(sizeof(MenuInfo) == 64, "MenuInfo layout");
// key_ui hooks (b): keepalive tick (true when the status text changed),
// status line, the decoded catalog (0 if unavailable), and the boot choice.
bool tick();
bool link_lost();
const char* status();
const char* command(uint32_t item);
const xyf::catalog::Catalog* catalog();
void choose_default();
void choose(uint32_t entry);
// Test 059: a check failed, so the menu opens without PLAY and never times out
// into the broken default (it waits for a choice). #21: also when Menu at power-on is always (the default
// checked good).
bool menu_forced();
// #197: A's doubt at the power-on check of the default (xyf_converge.hpp Doubt; 0: none). The menu then converges first
// (key_ui: the progress screen, then the default boots) and never times out. take_converge: the doubt, once per boot.
uint32_t take_converge();
#if defined(XYF_ONE_SESSION)
// Test 065 (b): the USB command plumbing on the file boot's session (menu_link delegates).
bool link_value(uint32_t value);          // one tagged command (raw value), answered by a's loop
bool link_up();
void link_patience(uint32_t ms);          // a write in progress: allow a long echo (0 = normal)
bool link_idle();
uint32_t link_keepalives();
bool refresh_catalog();                 // invalidate B's cache, then fetch A's current catalog
uint32_t storage_view();                // 193: xyf::menu::StorageView of A's storage (the menu's first row)
uint32_t clock_ms();                    // this boot's session clock (both roles)
// On b: B's file client (file_client.cpp) on this session. link_claim(): the session with
// its request slot free (an outstanding keepalive echo waited for, bounded), else 0: link lost, or
// an earlier file request still out past its caller's bound. link_step(): one poll; false once
// the link is lost. link_sent(): a file request left (the keepalive clock and
// the lost limit count from here). A file request still out lost_ms after it left = link lost
// (until a reboot; the menu says so and USB still answers).
static const uint32_t lost_ms = 60000;   // > the client's longest bound (45 s)
peer_services::Stock121PeerSession* link_claim();
bool link_step();
void link_sent();
// The last file request's answer (result, errno), copied when B's poll (link_step, link_close's wait) saw it
// complete: the request slot itself is reused by keepalives and menu commands (#144).
void link_answer(uint32_t& value, uint32_t& error);
// On b: close the link before b's stock reset (file_boot.cpp); true when its hardware is
// off. close_ms bounds the wait for a request still open (wait: false: not waited for).
static const uint32_t close_ms = 500;
bool link_close(bool wait);
#endif
// b: set the default to catalog entry (blocks at most write_ms); status text.
const char* set_default(uint32_t entry);
#if defined(XYF_STAGE2)
// Stage 2 "hello" (--xyf-stage2, FINDINGS-stage2.md): with PLAY held, after
// the catalog, b sends STAGE2; a reads xyf::stage2::path read-only, runs
// xyf::stage2::accept (header, window, CRC, SHA-256) and serves the file as a
// blob, or an 8-byte refusal blob ("XYSN", status | check << 8). b accepts it
// again, copies the image into the load window, verifies the copy, flushes
// the data cache and invalidates the instruction cache. key_ui then calls it
// once (xyf_stage2_enter) before the stage-1 menu. Read-only: no write of any
// kind. Never on a normal (no-PLAY) boot.
static const uint32_t item_stage2 = 0x12;
static const uint32_t stage2_ms = 15000;        // b: bound for the stage-2 blob (incl. a's read + hash)
enum Stage2Status : uint32_t {
    s2_none = 0, s2_ready = 1, s2_storage = 2, s2_open = 3, s2_read = 4, s2_check = 5,
    s2_link = 6, s2_copy = 7, s2_refused = 8
};
struct Stage2Info {
    uint32_t status, check, file_bytes, version;   // a: served/refused; b: received/accepted
    uint32_t entry, exit, load_ms, run_ms;          // b: entry called, exit code (0xffffffff not run)
};
static_assert(sizeof(Stage2Info) == 32, "Stage2Info layout");
// key_ui hooks (b): validated entry (0 if none), and the run's outcome.
uint32_t stage2_entry();
void stage2_done(uint32_t exit, uint32_t run_ms);
#if defined(XYF_STAGE2_MENU)
// Stage 2 main (ABI 2, --xyf-stage2-menu): the catalog blob exactly as a
// sent it (catalog_buffer; 0 and bytes = 0 when b could not decode it).
const uint8_t* catalog_blob(uint32_t& bytes);
#endif
#endif
} }
#endif
