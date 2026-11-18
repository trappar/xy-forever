#ifndef XYF_STORAGE_SERVER_HPP
#define XYF_STORAGE_SERVER_HPP
#include <stdint.h>
#include "cold_storage.hpp"
#include "file_engine.hpp"
#include "peer_services.hpp"
// Processor a's storage server: b's stock SPI file requests on the file boot's link session (RPC
// mode, Test 065), run by the stock slave executors on a's own storage session.
//
// File engine (docs/architecture/link.md): one engine mode serves every file
// request. Each request is decided by file_engine::decide_request, whose path policy
// (file_engine::decide_engine) is the same function b applies before it sends: a enforces it on
// every request, so no caller can bypass it. b enters the engine with one of three op-10/request
// command items:
//   command_item (3)      read-only: op 8 = a's read-only mount (the one shared with the file
//                         boot: adopt()), op 9 closes b's handles; every change is refused
//   file_write_item (10)  the write entry: op 8 is the read-only mount; the first request
//                         that changes storage opens the read-write window (cold_storage
//                         open_write_window: read-only -> read-write on the same device), which
//                         serves every request until it closes (yaffs checkpoint, gates back,
//                         read-only again): op 9 (which also returns to read-only), window_idle_ms
//                         without a request (tick), any other command item (end_writes), b gone
//   engine_run_item (6)   the same window, held by run_engine until b's engine_leave_item
// Served (class and op together; stock executors, each checked against the class's vtable):
//   partition 0x11907694: 8 mount, 9 unmount (answered by a itself), 0x2c lseek, 0x2d close,
//                         0x30 closedir (889f76ca)
//   path      0x119076b4: 0x29 open (flags 0: read; any other flags: a change), 0x2e opendir,
//                         0x21 access, 0x25 unlink, 0x26 mkdir, 0x27 rmdir (889f6fea); the path
//                         in the stock path buffer 0x8803f6e0. 0x21 with the argument
//                         rename_from / rename_to is file.rename (answered by a itself: the source
//                         kept, then the approved yaffs rename 889fa834 in the write window)
//   query     0x11907388: 0x22 stat, 0x23 statfs, 0x31 free space (889f72e4)
//   read      0x11907368: 0x2a read (889f6cd6)
//   sector    0x11907348: 0x2f readdir (889f6b7c)
//   write     0x11907710: 0x2b write (889f6dca)
// Handles: a tracks the files and folders it opened for b (max_fds each); every handle request
// must name one. Everything else gets a stock-shaped error reply (result -1, errno), so every b
// request gets its answer. The raw boot-partition layer is never served here. Per record
// 016 F1: a boot request (peer_services::boot_request) outside install mode, in every entry, and
// any raw-layer request in the run entry, is b's bootloader asking (b restarted alone): a gives no
// answer (the session holds it), closes storage and restarts; the rest of the raw layer is refused,
// or served by install mode below.
// Mount: never the stock op-8 path (889f75dc mounts read-write). a mounts with the proven
// write-denied fresh path (cold_storage begin_fresh) and registers only the stock yaffs provider
// (889f3910 with the stock provider vtable 0x11906448), which the stock posix layer needs.
// b (role 1) sends these requests with its own timed client (file_client.hpp): the
// stock request builders on its session's slot, never the stock posix client calls (their RPC
// 889f7b12 waits without a bound). a's own posix calls (stat, open, close: the marker check; close
// and closedir: b's leftovers) run on a, where they reach yaffs directly.
namespace xy_forever { namespace storage_server {
static const uint32_t magic = 0x31565352u;          // "RSV1"
static const uint32_t command_item = 3;             // command item: the engine, read-only
static const uint32_t settle_ms = 50;               // b: quiet after the command so a observes it
static const uint32_t max_log = 16, max_fds = 4, max_path = 240;
static const uint32_t partition_class = 0x11907694u, path_class = 0x119076b4u, read_class = 0x11907368u;
static const uint32_t path_buffer = 0x8803f6e0u;    // stock slave path body (889f8d94 stage 1)
enum ErrorCode : uint32_t { error_badf = 9, error_access = 13, error_busy = 16, error_mfile = 24, error_rofs = 30 };
// XY Forever USB install (XYF_USB_SERVICE): after b's op-10 command
// install_item, a additionally answers exactly the requests the stock boot
// partition writer mmc_tools 88a06a7e (role 1, dev-menu Track 5) and the
// read-back emit, routed to the stock executors:
//   partition 0x11907694: 0x18 select (argument 0, 1 or 2 only),
//                         0x14 trim (only while partition 1 or 2 is selected)
//   sector    0x11907348: 0x11 / 0x12 read (any partition: reads only)
//   write     0x11907710: 0x15 sector 0 / 0x16 sectors (only while 1 or 2 is
//                         selected: the eMMC boot partitions; never 0 = user)
// While a boot partition is selected every file request is refused (yaffs
// lives on partition 0), in every engine entry (file_engine refuse_partition), and so are
// the read-write window and the state write. b switches back to 0 after every install, failed
// or not; if that fails too, a refuses storage until it restarts. Generic ops 1/3/5/7/10 are answered by the stock
// slave ISR itself (3 and 5 are read-only: 88a0759a, 88a086c0).
static const uint32_t install_item = 4, reboot_item = 5;
// USB service started (Test 041): no action on a; it makes commands_seen
// nonzero so a restarts with b if the session is then lost (menu_link).
static const uint32_t usb_mode_item = 7;
// Retired: a's own guard (Test 042, guard_item 8; guard off 12) and
// diag.restart_a (13). b no longer sends them; the numbers stay reserved (8 still keeps the window).
static const uint32_t guard_item = 8;
static const uint32_t dry_run_item = 9;        // usb_service.hpp: a resets instead of starting the OS
static const uint32_t sector_class = 0x11907348u, write_class = 0x11907710u;
enum InstallAction : uint32_t {
    install_none = 0, install_select, install_trim, install_read, install_write,
    install_refuse_partition, install_refuse_selected, install_refuse_op, install_refuse_files
};
// Pure decision for install-mode requests (host-tested). argument = word +8.
// Returns install_none for requests the read-only allowlist handles.
InstallAction decide_install(uint32_t vtable, uint32_t op, uint32_t argument, uint32_t selected);
// b's READ_FILE path format: "/yaffs2/<...>", NUL within max_path, no empty, "." or ".."
// components (file.read's bad_request). b then applies the engine's path policy (refused).
bool valid_path(const char* path);
#if defined(XYF_MTP)
// The engine's run entry (097: the storage self-test).
static const uint32_t engine_run_item = 6;
#endif
#if defined(XYF_USB_FILE_WRITE)
// XY Forever USB file write (Test 049, --xyf-product): file_write_item enters the engine's
// write entry (one read-write window). b writes only in the write area below (a's policy is the engine's).
static const uint32_t file_write_item = 10;
static const uint32_t write_open_flags = 0x8301u;   // 0803c928 for "wb": table[2]=1 | 0x200 | 0x100 | 0x8000
// The USB write area (since 1.13): every valid_path() below /yaffs2/;
// decide_engine refuses changing the marker and anything in /yaffs2/upgrade (file_engine.hpp), and A also
// refuses a change that reaches either through a link. write_root: the root's length (8), else 0.
uint32_t write_root(const char* path);
// XY Forever's own content job keeps the old, narrower area: under "/yaffs2/xy-forever/" or
// "/yaffs2/content/" with a non-empty rest, no name holding "bootloader" (ASCII case ignored), never user/.
// The root's length (19 or 16), else 0.
uint32_t own_area(const char* path);
// file.delete / file.rmdir / file.mkdir: the write area and the engine's policy for op.
bool valid_change_path(const char* path, file_engine::Operation op);
// FILE_WRITE's paths: the write area, allowed by the engine's policy for a write open.
bool valid_write_path(const char* path);
// file.rename: both paths in the write area and the engine's policy for the pair (B before it
// sends; A again before it renames).
bool valid_rename(const char* from, const char* to);
#endif
#if defined(XYF_USB_FILE_WRITE)
// a closes an idle window on its own after window_idle_ms (the fallback: b closes it with op 9
// after window_settle_ms without a write, before a would, so a's loop never leaves b's probes
// unanswered while yaffs writes its checkpoint). Op 9's reply: 1, or 3 when it closed a window.
static const uint32_t window_idle_ms = 10000, window_settle_ms = 8000;
#endif
#if defined(XYF_MTP) || defined(XYF_USB_FILE_WRITE)
// Both roles: CRC of every span in mtp_spans.hpp (the whole stock path of the engine on both
// processors; bit i set, folded; 0 = ok).
uint32_t engine_identity();
#endif
struct Result {
    uint32_t magic, version, role, enabled;
    uint32_t identity_mismatch, mount_begin, mount_end, mount_safe;
    uint32_t registered, requests, executed, refused;
    uint32_t open_fds, heap_before, heap_after, denied_writes;
    // b: b_window = op 9's reply when b closed the write window (1 none was open, 3 closed
    // one, 0 failed, 0xffffffff no answer), b_windows = windows closed that way, b_catalogs =
    // catalogs fetched after writes.
    uint32_t reads, b_window, b_fd, b_bytes;
    uint32_t b_close, b_windows, b_catalogs, log_count;
    uint32_t text[8];                 // b: first line read (NUL padded)
    uint32_t log[max_log];            // a: op | action << 8 | (result & 0xffff) << 16
    cold_storage::Diagnostic storage;
};
extern Result result;
// Role 0: before the held session's begin(); storage must outlive the session.
void install(peer_services::Stock121PeerSession& session, cold_storage::Stock121ReadOnlySession& storage);
// Role 0: after the session loop (b never sent op 9): close leftovers and the read-write window
// then unmount.
void finish(cold_storage::Stock121ReadOnlySession& storage);
#if defined(XYF_ONE_SESSION)
// Test 065 (a): the caller's storage session is already mounted (fresh, read-only) and lives as
// long as the link session: op 8 registers the provider once, op 9 only closes b's files.
void adopt();
// Before the shared mount ends (a boot, the session's end, a reset): the read-write window (yaffs
// checkpoint, read-only again; a reset or boot with it open is a power cut for yaffs), then
// b's open files.
void close_files(cold_storage::Stock121ReadOnlySession& storage);
#endif
// Role 0: the read-only mount shared by the file server and the state write
// (one mount per boot); true when mounted; false while install mode has a boot partition
// selected. held_files(): files and folders b holds open.
bool ensure_mounted(cold_storage::Stock121ReadOnlySession& storage);
uint32_t held_files();
#if defined(XYF_USB_FILE_WRITE)
// Role 0, a's loop between polls: tick closes the write window after window_idle_ms without
// a request (now: a's clock); end_writes closes it and ends the write entry before any other
// command item (catalog, check, boot, set default, install, dry run, reboot); library_changed is
// true once after a window that was opened has closed (the caller rescans and re-encodes the catalog).
void tick(cold_storage::Stock121ReadOnlySession& storage, uint32_t now);
void end_writes(cold_storage::Stock121ReadOnlySession& storage);
bool library_changed();
#endif
#if defined(XYF_MTP) && defined(__bfin__)
// Role 0 (097): the engine's run entry (no idle window, no cap). Returns when b sends
// engine_leave_item (read-write window closed, back to the read-only entry). It restarts a
// (storage closed first) when b's bootloader asks (the session's follow_now), or when the session fails.
static const uint32_t engine_leave_item = 11;
void run_engine(peer_services::Stock121PeerSession& session, cold_storage::Stock121ReadOnlySession& storage);
#endif
} }
#endif
