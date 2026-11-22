#ifndef XYF_COLD_STORAGE_HPP
#define XYF_COLD_STORAGE_HPP
#include "boot_services.hpp"
namespace xy_forever { namespace cold_storage {
using services::U32;
// Caller-owned diagnostic record. Never place it at an assumed free physical
// address. capture() only reads retained state and MMIO; begin() changes RAM,
// controller/card configuration and mounts YAFFS with media writes intercepted.
struct Snapshot {
    U32 role, stock_mounted, local_mounted, initialized, references, handle;
    U32 interrupt_mask, source_control, vector_index, vector_handler, vector_context;
    U32 rsi_control, rsi_mask, rsi_status, rsi_configuration, rsi_clock;
    U32 completion, completion_initialized, completion_count;
    U32 adma_control, adma_pointer, transfer_address, transfer_bytes, transfer_error;
    U32 log_callbacks[4];
    U32 device, mounted, readonly;
};
enum class Result {
    ok,
    unavailable,
    identity,
    role,
    lifecycle,
    irq,
    device,
    busy,
    activate,
    mount,
    write_attempt,
    open_handles,
    unmount,
    shutdown,
    write_refused,
    write_failed,
    verify_failed,
    remount      // state slot write (XY Forever)
};
enum class Stage { idle, captured, guarded, activated, mounted, closed, failed };
struct Diagnostic {
    U32 magic, version;
    Stage stage;
    Result result;
    U32 denied_writes, handles;
    Snapshot before, after;
};
// These are necessary retained-state checks. They do not assert that a card or
// its interrupt can make forward progress. Stock primitives have unbounded waits.
// Observed normal-boot cold state (Tests 008/009): the bootloader leaves the
// controller initialized with its reference held, and registers no YAFFS
// device (device must be 0).
Result check_fresh(const Snapshot& state);
// Retained state reads only: no identity CRCs and no device lookup.
bool capture_state(Snapshot& output);
// RAM-only: CRC-check the stock constructor/allocator/register chain, then
// construct and register /yaffs2 exactly as stock filesystem_mount does
// (889f963e("/yaffs2", 0, 0x11ff)). No controller init, mount or media access.
// Requires check_fresh and an empty device list; verifies the result with the
// bounded lookup and the fresh-device shape. Device output is 0 on failure.
Result construct_fresh(U32& device, U32& shape_mismatch);
// Post-mount check for the fresh path: controller untouched (no init, same
// reference count), the constructed device mounted read-only.
Result check_fresh_active(const Snapshot& before, const Snapshot& after);
// Bit i set: expected fresh-device field i differs (0 = exact stock shape).
U32 fresh_shape_mismatch(U32 device);

class Stock121ReadOnlySession {
public:
    explicit Stock121ReadOnlySession(Diagnostic& diagnostic);
    // Normal-boot path (Tests 008-011): construct/register /yaffs2, deny every
    // media-change callback before mount, mount read-only. Never calls
    // controller init (a no-op when open) or shutdown; end() leaves the
    // controller open as every working OS handoff did.
    Result begin_fresh(services::Phase phase);
    // 191: after a begin_fresh whose read-only mount failed (cleanup ok), mount read-only again on
    // the same constructed device, gated as begin_fresh. busy when not in that state (nothing changes).
    Result retry_fresh();
    U32 construct_mismatch() const { return mismatch_; }
    // Direct read-only YAFFS API for the fresh path. The stock stdio/directory
    // layer dispatches through a provider registry that only provider_mount
    // (a writable mount) populates; its generic readdir/closedir panic when a
    // handle has no provider. These calls need ready() and a separate CRC gate
    // over the eight entry points; a gate failure disables I/O only.
    bool direct_ready() const { return direct_ok_ && ready(); }
    bool direct_gate() const { return direct_ok_; }
    int direct_open(const char* path);                 // O_RDONLY; fd or <0
    int direct_read(int fd, services::Byte* out, U32 count);
    int direct_close(int fd);
    U32 direct_opendir(const char* path);              // 0 on failure
    const services::Byte* direct_readdir(U32 dir);     // yaffs_dirent or 0
    int direct_closedir(U32 dir);
    int direct_error() const;
    int mount_status() const { return mount_status_; }
    // XY Forever state slot write (the only write path; never on a normal boot).
    // Fresh session only, mounted read-only and ready(), no open handles.
    // path must be exactly /yaffs2/xy-forever/state-a.txt or .../state-b.txt,
    // 0 < count <= 4096, and /yaffs2/xy-forever must already exist (mkdir is
    // not identified). Sequence: unmount RO (gates on) -> restore the stock
    // media callbacks -> mount RW -> open O_WRONLY|O_CREAT|O_TRUNC (0x241, mode
    // 0x180: exactly stock "wb") -> write -> close -> read back and compare ->
    // unmount RW -> re-install the deny gates -> mount RO again (ready()).
    // Any failure stops at the first safe point: RW is always unmounted and the
    // gates re-installed before returning; a failed RO remount leaves the
    // session unmounted (end() is still ok). readback: caller buffer >= count + 1
    // (one extra byte detects a longer file).
    // step: last step reached (diagnostics).
    Result write_state_slot(const char* path, const services::Byte* data, U32 count, services::Byte* readback,
                            U32& step);
    static bool state_slot_path(const char* path);      // exact allowlist (host-tested)
#if defined(XYF_MTP) || defined(XYF_USB_FILE_WRITE)
    // XY Forever MTP mode (a, product; Test 049 also the USB file write). open_write_window: fresh
    // session, mounted read-only and ready(), no direct handles; CRC gates
    // (identity, direct, write spans), then unmount RO (gates on) -> restore
    // the stock media callbacks -> mount RW, and leave it mounted read-write
    // for the stock posix layer. On failure it returns to the gated, unmounted
    // state (end() ok). close_write_window: unmount RW (yaffs checkpoint) and
    // re-install the deny gates; then end() as usual. A failed RW unmount
    // leaves the stage failed (never hand off). step: diagnostics.
    Result open_write_window(U32& step);
    Result close_write_window();
#if defined(XYF_ONE_SESSION)
    // Resume the shared session only after the write window is closed and gated.
    // A failed RW unmount is never eligible for a read-only remount.
    Result reopen_read_only();
    // 192: after a begin_fresh/retry_fresh whose read-only mount failed with write_attempt (yaffs
    // wants to repair after an unclean shutdown): the CRC gates, then with the stock media callbacks
    // (end() put them back) mount RW (yaffs repairs), unmount (checkpoint), the callbacks as before,
    // then mount_guarded() as begin_fresh. busy when not in that state (nothing changes). A failed RW
    // unmount leaves the stage failed and mounted_ set (never hand off). step: how far it got.
    Result repair_fresh(U32& step);
#endif
#endif
    Result end();
    bool ready() const;
    // A failed unmount retains the barriers and cannot fall through to an OS.
    // After every attempted session, require end()==ok and this predicate before
    // executing a payload. It does not qualify physical RAM or peer state.
    bool safe_to_handoff() const;
    static bool capture(Snapshot& output);

private:
    Stock121ReadOnlySession(const Stock121ReadOnlySession&);
    Stock121ReadOnlySession& operator=(const Stock121ReadOnlySession&);
    Diagnostic& diagnostic_;
    U32 device_, saved_[5], saved_logs_[4];
    bool guarded_, activated_, mounted_, attempted_, fresh_, built_;
    U32 mismatch_;
    int mount_status_;
    bool direct_ok_;
    Result fail(Result result);
    Result mount_guarded();
};
}}
#endif
