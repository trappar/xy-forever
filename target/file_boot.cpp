#include "cold_layout.hpp"
#include "xyf_clock.hpp"
#include "file_boot.hpp"
#include "cold_heap.hpp"
#include "storage_probe.hpp"
#include "warm_snapshot.hpp"
#include "../native/ldr.hpp"
#include "../native/trace_slot.hpp"
#include "../native/hash.hpp"
#include "xyf_menu.hpp"
#if defined(XYF_USB_SERVICE)
#include "usb_service.hpp"
#endif
#include "xyf_crc32.hpp"
#include "xyf_cold_library.hpp"
#if defined(XYF_STATE_WRITE) || defined(XYF_ONE_SESSION)
#include "xyf_state_write.hpp"
#endif
#if defined(XYF_ONE_SESSION)
#include "storage_server.hpp"   // Test 065: a serves b's file requests in this session
#endif
#if defined(XYF_STAGE2)
#include "xyf_stage2.hpp"
#endif
#include "key_ui.hpp"            // xy_forever_key_ui(): the menu on b
// Test025 (see file_boot.hpp).
namespace xy_forever { namespace file_boot {
// Initialized data, not BSS: the cold link has no zero-initialization.
Result result __attribute__((section(".data"))) = {magic};
} }
// Storage-image handoff read by cold.cpp: set to
// 1 only after a commit and an exact copy to the RAM-boot source.
extern "C" {
volatile uint32_t xy_forever_storage_image_loaded = 0xffffffffu;
volatile uint32_t xy_forever_storage_image_bytes __attribute__((section(".data"))) = 0;
volatile uint32_t xy_forever_storage_image_crc __attribute__((section(".data"))) = 0;
extern const xyf_core::Policy* volatile xy_forever_cold_policy;
}
extern "C" char xy_forever_module_end[] __asm__("__end");

namespace {
using xyf_core::U32;
using xyf_core::Byte;
using namespace xy_forever;
using namespace xy_forever::file_boot;
typedef peer_services::Result SessionResult;

U32 cycles_div1024() {
    U32 low, high;
    __asm__ volatile("%0 = CYCLES; %1 = CYCLES2;" : "=d"(low), "=d"(high));
    return (low >> 10) | (high << 22);
}
// 400 MHz core clock: ms = units * 0.00256 ~= units * 21 / 8192 (see key_ui).
volatile U32 start_ms __attribute__((section(".data"))) = 0;   // xyf_clock: wrap-safe, no session cap needed
U32 now() { return xyf::clock::ms() - start_ms; }
Byte byte(U32 address) { return *reinterpret_cast<const volatile Byte*>(address); }

// Table-driven (xyf_crc32): this runs up to four times over a 2-4 MB image.
U32 crc32(const volatile Byte* data, U32 count) { return xyf::crc::update_volatile(0, data, count); }

const U32 paint = 0x5ca1ab1eu, stack_top = XYF_STACK_END;
U32 stack_pointer() { U32 sp; __asm__ volatile("%0 = SP;" : "=d"(sp)); return sp; }
U32 paint_stack() {
    U32 begin = (reinterpret_cast<U32>(xy_forever_module_end) + 3u) & ~3u;
#if defined(XYF_STACK_PAINT_FLOOR)
    if (begin < XYF_STACK_PAINT_FLOOR) begin = XYF_STACK_PAINT_FLOOR;
#endif
    const U32 end = (stack_pointer() - 256u) & ~3u;
    for (U32 a = begin; a < end; a += 4) *reinterpret_cast<volatile U32*>(a) = paint;
    return begin;
}
U32 stack_used(U32 begin) {
    U32 a = begin;
    while (a < stack_top && *reinterpret_cast<volatile U32*>(a) == paint) a += 4;
    return stack_top - a;
}

// Same admission on both processors, so the two boot decisions cannot differ
// on content: cold LDR policy and entry (as cold.cpp re-checks), the exact
// report slot (dry run: expected -> expected, no change) and, in census
// builds, the zero census slot. Both later patches in cold.cpp therefore
// succeed for an admitted image.
#if defined(XYF_PLAIN_IMAGES)
// Test 051: 1 when the last admitted image is plain (an official os.ldr: no
// report slot, so no reporter); cold.cpp then boots it without the report patch.
extern "C" { volatile uint32_t xy_forever_storage_image_plain __attribute__((section(".data"))) = 0; }
#endif

U32 admit(Byte* image, U32 bytes) {
    const xyf_core::Policy* policy = xy_forever_cold_policy;
#if defined(XYF_PLAIN_IMAGES)
    xy_forever_storage_image_plain = 0;
#endif
    if (!policy || bytes < 32 || bytes > staging_bytes - 512) return reason_size;
    xyf_core::Summary summary;
    if (xyf_core::inspect(image, bytes, *policy, summary) != xyf_core::Error::ok) return reason_policy;
    if (summary.entry != 0x11a00000u) return reason_entry;
    const xyf_core::trace_slot::Result slot = xyf_core::trace_slot::replace(
        image, bytes, *policy, storage_probe::carry_base, xy_forever_trace_expected,
        xy_forever_trace_expected, sizeof(storage_probe::Report));
#if defined(XYF_PLAIN_IMAGES)
    // Test 051: no report slot at all = a plain official image. Same cold policy
    // and entry as every image; no reporter, so no report or census slot needed.
    if (slot.status == xyf_core::trace_slot::Status::missing_slot) {
        xy_forever_storage_image_plain = 1;
        result.library.flags |= 8u;                    // plain image admitted
        return reason_ok;
    }
#endif
    if (slot.status != xyf_core::trace_slot::Status::ok) return reason_report_slot;
    if (xyf_core::trace_slot::locate_zero(image, bytes, *policy, XYF_CENSUS_BASE,
                                         XYF_CENSUS_BYTES).status != xyf_core::trace_slot::Status::ok)
        return reason_census_slot;
    return reason_ok;
}

// After a commit: copy the agreed image to the RAM-boot source and hand it to
// cold.cpp. The staged bytes' CRC was fixed before agreement.
bool hand_off(U32 bytes, U32 crc) {
    Result& r = result;
#if defined(XYF_MENU_ON_FAILURE)
    const U32 handoff_start = now();
#endif
    const volatile U32* from = reinterpret_cast<const volatile U32*>(staging);
    volatile U32* to = reinterpret_cast<volatile U32*>(boot_buffer);
    for (U32 i = 0; i != (bytes + 3) / 4; ++i) to[i] = from[i];
    r.copy_crc = crc32(reinterpret_cast<const volatile Byte*>(boot_buffer), bytes);
#if defined(XYF_MENU_ON_FAILURE)
    if (r.role == 1) {
        r.timing.b_handoff_ms = now() - handoff_start;
        r.timing.valid |= xyf_boot::timing_handoff;
    }
#endif
    if (r.copy_crc != crc) return false;
    xy_forever_storage_image_bytes = bytes;
    xy_forever_storage_image_crc = crc;
    return true;
}

// ---- XY Forever menu boot (see xyf_boot.hpp) --------------------------------
using peer_services::Stock121PeerSession;
const U32 keepalive_ms = 250, answer_ms = 500, keepalive_tag = 0x4b41u;   // as the Test024 held link
const U32 handshake_ms = 2000;                                              // b: Test023 took 540 ms
static_assert(xyf_boot::scan_capacity >= xyf::library::scratch_needed, "scan scratch");
static_assert(sizeof(xyf::library::Library) <= xyf_boot::scan_buffer - xyf_boot::library_buffer, "library");
static_assert(sizeof(xyf::catalog::Catalog) <= xyf_boot::scan_buffer - xyf_boot::library_buffer, "catalog");
static_assert(xyf::catalog::max_bytes <= xyf_boot::catalog_capacity, "catalog buffer");

#include "file_boot_b_link.ipp"   // B: link state, poll, settle, ask, send

// a: path and info.txt promises for library entry index.
bool entry_selection(const xyf::library::Library& lib, U32 index, xyf::cold_library::Selection& out) {
    for (U32 i = 0; i != sizeof(out); ++i) reinterpret_cast<char*>(&out)[i] = 0;
    if (index >= lib.count || !lib.entries[index].usable) return false;
    const xyf::library::Entry& e = lib.entries[index];
    if (!xyf::library::detail::path(out.path, "/yaffs2/xy-forever/firmware/", e.id, "/os.ldr")) return false;
    out.index = index;
    out.has_bytes = e.has_info && e.info.has_bytes; out.bytes = e.info.bytes;
    out.has_crc32 = e.has_info && e.info.has_crc32; out.crc32 = e.info.crc32;
    return true;
}
// a: read the selected image into staging (bounded).
U32 read_image(cold_storage::Stock121ReadOnlySession& storage, const char* path, U32& bytes) {
    bytes = 0;
    const int fd = storage.direct_open(path);
    if (fd < 0) return reason_open;
    Byte* target = reinterpret_cast<Byte*>(staging);
    const U32 capacity = staging_bytes - 512;          // client keeps the stock extra sector
    U32 reason = reason_ok;
    int got = 0;
    for (U32 guard = 0; guard != 0x200; ++guard) {
        const U32 want = capacity - bytes < 0x10000u ? capacity - bytes : 0x10000u;
        if (!want) { reason = reason_size; break; }
        got = storage.direct_read(fd, target + bytes, want);
        if (got <= 0) break;
        bytes += U32(got);
    }
    result.read_result = U32(got) & 0xffu;
    if (storage.direct_close(fd) || got < 0) reason = reason ? reason : reason_read;
    return reason;
}

#if defined(XYF_STAGE2)
// ---- Stage 2 "hello" (see xyf_boot.hpp, FINDINGS-stage2.md) -----------------
// Read-only on a, a blob over the held link, then copy + cache maintenance on
// b. Every failure leaves the stage-1 menu and boot paths exactly as before.
volatile U32 b_stage2_entry __attribute__((section(".data"))) = 0;
static_assert(xyf::stage2::window_end <= staging && xyf::stage2::window_begin >= XYF_STACK_END,
              "stage-2 window overlaps the module/stack or the transfer staging");
static_assert(xyf::stage2::window_end <= xyf_boot::catalog_buffer, "stage-2 window overlaps the catalog");

// a: answer STAGE2 with the accepted file, or an 8-byte refusal blob.
__attribute__((noinline)) void a_serve_stage2(Stock121PeerSession& session, cold_storage::Stock121ReadOnlySession& storage, bool mounted) {
    xyf_boot::Stage2Info& s = result.stage2;
    U32 status = xyf_boot::s2_storage, bytes = 0;
    U32 check = 0;
    if (mounted && storage.direct_ready()) {
        const U32 reason = read_image(storage, xyf::stage2::path, bytes);
        s.file_bytes = bytes;
        if (reason == reason_open) status = xyf_boot::s2_open;
        else if (reason != reason_ok) status = xyf_boot::s2_read;
        else {
            xyf::stage2::Header header;
            const xyf::stage2::Check c = xyf::stage2::accept(reinterpret_cast<const Byte*>(staging), bytes,
                                                             xyf::stage2::this_stage1, header);
            check = U32(c);
            if (c == xyf::stage2::Check::ok) { status = xyf_boot::s2_ready; s.version = header.version; s.entry = header.entry; }
            else status = xyf_boot::s2_check;
        }
    }
    s.status = status; s.check = check;
    if (status != xyf_boot::s2_ready) {
        volatile U32* note = reinterpret_cast<volatile U32*>(staging);
        note[0] = xyf::stage2::refusal_magic; note[1] = status | check << 8;
        bytes = 8;
    }
    session.touch(now());
    if (session.freeze_blob(reinterpret_cast<const Byte*>(staging), bytes) != SessionResult::ok)
        s.status |= 0x100u;                              // could not freeze: b times out, both boot built-in
    s.load_ms = now();
}

// b: data-cache flush, then instruction-cache invalidate, over [begin, end).
__attribute__((noinline)) void prepare_code(U32 begin, U32 end) {
    for (U32 a = begin & ~31u; a < end; a += 32) __asm__ volatile("FLUSH [%0];" : : "a"(a) : "memory");
    __asm__ volatile("SSYNC;" : : : "memory");
    for (U32 a = begin & ~31u; a < end; a += 32) __asm__ volatile("IFLUSH [%0];" : : "a"(a) : "memory");
    __asm__ volatile("SSYNC; CSYNC;" : : : "memory");
}
#endif

#if defined(XYF_ONE_SESSION)
// a: before a reset on b's command, let the answer to that command leave. poll() only
// queued it, and the stock reset disables interrupts at once; without the answer b would
// wait command_ms before it resets too. Bounded; b resets 100 ms after the answer.
void a_answer_out(Stock121PeerSession& session) {
    const U32 start = now();
    while (session.answering() && now() - start < xyf_boot::answer_out_ms) {}
    const U32 quiet = now();
    while (now() - quiet < xyf_boot::answer_quiet_ms) {}
}
#endif

// a (role 0). Returns the commit decision; bytes/crc describe the served image.
#if defined(XYF_MENU_LINK)
// Product build (Test 044): after the last image sector b admits the image
// (SHA-256 + LDR check over ~3.5 MB) before it sends restore; that took longer
// than a's 3 s idle rule, so a gave up and both booted the embedded copy.
const U32 product_role0_idle_ms = 10000;
#endif
// Test 065 (a): b's files open through the storage server close before the shared mount ends;
// The read-write window too, on every path out (boot, session end, reboot, alone reset).
#if defined(XYF_ONE_SESSION)
inline void a_close_files(cold_storage::Stock121ReadOnlySession& storage) { storage_server::close_files(storage); }
#else
inline void a_close_files(cold_storage::Stock121ReadOnlySession&) {}
#endif
#if defined(XYF_ONE_SESSION)
// a follows b when b's bootloader asks (F1): close storage (the read-write window first) and
// restart, so a's bootloader serves b's next boot (it answers the request the session holds).
// Never between a request's executor and its answer: only from this loop, between polls. A b
// silent past the session's idle window only ends a's session (window closed, no restart).
void a_follow(cold_storage::Stock121ReadOnlySession& storage) {
    a_close_files(storage);
    usb_service::reset_self();
}
xyf::state_write::Collector a_collector __attribute__((section(".data"))) = {0, 0};
#endif
#if defined(XYF_MENU_ON_FAILURE)
// Test 059 (a): index of the staged, checked image (0xffffffff = none).
volatile U32 a_prepared __attribute__((section(".data"))) = 0xffffffffu;

U32 a_default_index(xyf::library::Library& lib) {
    lib.state.once[0] = 0;                              // no "once" while read-only
    uint32_t choice = 0;
    return xyf::library::boot_choice(lib, choice) == xyf::library::Choice::menu ? 0xffu : U32(choice);
}

// A default (index >= count) or entry that is listed with its os.ldr but whose content is not
// installed: reason_content, or reason_content_repair while a content switch is unfinished; index becomes
// that entry's (the menu's line names it). Else reason_no_library.
U32 a_content_reason(const xyf::library::Library& lib, U32& index) {
    const int32_t i = index < lib.count ? int32_t(index) : xyf::library::find(lib, lib.state.default_id);
    if (i < 0 || !lib.entries[i].image_present || !lib.entries[i].content_missing) return reason_no_library;
    index = U32(i);
    return lib.content.kind == xyf::content::switching ? reason_content_repair : reason_content;
}

// Read, match against info.txt and admit entry index into staging; storage stays
// mounted (admission only inspects memory). Records the library fields.
U32 a_prepare(cold_storage::Stock121ReadOnlySession& storage, bool mounted, const xyf::library::Library& lib,
              U32 index, U32& bytes, U32& crc) {
    Result& r = result;
    xyf_boot::TimingInfo& timing = r.timing;
    timing.valid &= xyf_boot::timing_mount | xyf_boot::timing_scan;
    timing.a_read_ms = timing.a_crc_ms = timing.a_admit_ms = timing.a_sha_ms = timing.a_freeze_ms = 0;
    r.library.index = index;
    r.admit_reason = reason_not_run;
    xyf::cold_library::Selection selection;
    U32 reason = reason_no_library;
    if (mounted && storage.direct_ready() && entry_selection(lib, index, selection)) {
        const char* id = lib.entries[index].id;
        for (U32 i = 0; i != 31; ++i) r.library.id[i] = id[i];
        r.library.expected_bytes = selection.bytes; r.library.expected_crc = selection.crc32;
        r.library.flags = (selection.has_bytes ? 1u : 0u) | (selection.has_crc32 ? 2u : 0u);
        U32 phase = now();
        reason = read_image(storage, selection.path, bytes);
        timing.a_read_ms = now() - phase; timing.valid |= xyf_boot::timing_read;
        if (reason == reason_ok) {
            phase = now();
            crc = crc32(reinterpret_cast<const volatile Byte*>(staging), bytes);
            timing.a_crc_ms = now() - phase; timing.valid |= xyf_boot::timing_crc;
            const bool matched = xyf::cold_library::matches(selection, bytes, crc);
            r.library.flags |= matched ? 4u : 0u;
            if (!matched) reason = reason_mismatch;
        }
        if (reason == reason_ok) {
            phase = now();
            r.admit_reason = admit(reinterpret_cast<Byte*>(staging), bytes);
            timing.a_admit_ms = now() - phase; timing.valid |= xyf_boot::timing_a_admit;
            if (r.admit_reason != reason_ok) reason = reason_policy;
        }
    }
    r.load_reason = reason;
    return reason;
}
#endif

#if defined(XYF_ONE_SESSION)
// 191: A's storage start for B (the catalog's reserved word, xyf_boot.hpp) and its mount attempts.
volatile U32 a_storage_word __attribute__((section(".data"))) = 0;
volatile U32 a_attempts __attribute__((section(".data"))) = 0;
volatile U32 a_failure_served __attribute__((section(".data"))) = 0;   // B has seen the failed mount
// 192: A's storage repair this boot (xyf_boot::repair_*): pending after a read-only mount failed with
// write_attempt; done or failed after the one attempt (then no more mounts this boot).
volatile U32 a_repair __attribute__((section(".data"))) = 0;
U32 cap255(U32 v) { return v > 0xffu ? 0xffu : v; }
void a_note_mount(cold_storage::Stock121ReadOnlySession& storage, U32 outcome, U32 took_ms) {
    ++a_attempts;
#if defined(XYF_MTP) || defined(XYF_USB_FILE_WRITE)
    if (a_repair == xyf_boot::repair_none && outcome == U32(cold_storage::Result::write_attempt))
        a_repair = xyf_boot::repair_pending;          // only this failure: yaffs wants to repair
#endif
    const U32 code = outcome ? cap255(outcome) : storage.direct_ready() ? 0u : xyf_boot::storage_unreadable;
    const U32 denied = result.storage.denied_writes > 63 ? 63u : result.storage.denied_writes;
    a_storage_word = code | cap255(a_attempts) << 8 | denied << 16 | a_repair << 22 |
                     cap255(took_ms >> 10) << 24;   // 1.024 s units (no division)
}
#if defined(XYF_MTP) || defined(XYF_USB_FILE_WRITE)
// 192: the storage repair (repair_fresh: read-write mount, unmount, read-only mount), once per boot,
// only after a write_attempt failure. True when mounted read-only after it.
bool a_repair_mount(cold_storage::Stock121ReadOnlySession& storage) {
    const U32 start = now();
    services::U32 step = 0;
    const cold_storage::Result m = storage.repair_fresh(step);
    result.storage_begin = U32(m);
    a_repair = m == cold_storage::Result::ok ? xyf_boot::repair_done : xyf_boot::repair_failed;
    a_note_mount(storage, U32(m), now() - start);
    return m == cold_storage::Result::ok;
}
#endif
// 191: A's mount failed: mount read-only again (retry_fresh) before a catalog. True when mounted.
// 192: none after the repair (done: mounted; failed: final this boot).
bool a_retry_mount(cold_storage::Stock121ReadOnlySession& storage) {
    if (a_repair >= xyf_boot::repair_done) return false;
    const U32 start = now();
    const cold_storage::Result m = storage.retry_fresh();
    result.storage_begin = U32(m);
    a_note_mount(storage, U32(m), now() - start);
    return m == cold_storage::Result::ok;
}
// a: scan the library again and re-encode the catalog b fetches (its byte count).
U32 a_rescan(cold_storage::Stock121ReadOnlySession& storage, bool mounted, xyf::library::Library& lib) {
    Result& r = result;
    xyf::library::detail::reset(lib);
    r.menu.scan_error = 0xffu;
    if (mounted && storage.direct_ready()) {
        const xyf::library::Error error = xyf::library::scan_direct(storage, lib,
            reinterpret_cast<Byte*>(xyf_boot::scan_buffer), xyf_boot::scan_capacity);
        r.menu.scan_error = U32(error);
        if (error != xyf::library::Error::ok) xyf::library::detail::reset(lib);
    }
    r.library.count = lib.count; r.library.skipped = lib.skipped;
    r.menu.catalog_bytes = xyf::catalog::encode(lib, reinterpret_cast<Byte*>(xyf_boot::catalog_buffer),
                                                xyf_boot::catalog_capacity, a_storage_word);
    return r.menu.catalog_bytes;
}

static_assert(xyf_boot::role0_cap_ms == peer_services::no_cap && xyf_boot::role1_menu_cap_ms == peer_services::no_cap,
              "v3 link sessions have no cap");
#endif
#include "file_boot_a_menu.ipp"   // A: storage session, command dispatch, image ownership

#include "file_boot_b_menu.ipp"   // B: checks, catalog, stage 2 load, role1_menu_boot
}


#if defined(XYF_TFW_CHECK)
namespace xy_forever { namespace file_boot {
void discard_prepared() { a_prepared = 0xffffffffu; }
} }
#endif

#include "file_boot_b_api.ipp"    // B: the xyf_boot functions

extern "C" void xy_forever_file_boot() {
    Result& r = result;
#if defined(XYF_STAGE2)
    r.version = 4;
    r.stage2.exit = 0xffffffffu;
#elif defined(XYF_MENU_ON_FAILURE)
    r.version = 6;
    r.timing.magic = xyf_boot::timing_magic; r.timing.version = 1;
    r.detail.magic = xyf_boot::timing_detail_magic; r.detail.version = 1;
#else
    r.version = 3;                                     // 1 and 2: the file and library boots before the menu
#endif
    r.start_cycles = cycles_div1024();
    start_ms = xyf::clock::ms();
    r.role = byte(0x8807cac4u);
    r.begin_result = r.poll_result = r.end_result = U32(SessionResult::unavailable);
    r.storage_begin = r.storage_end = U32(cold_storage::Result::unavailable);
    r.load_reason = r.admit_reason = reason_not_run;
    xy_forever_storage_image_loaded = 0;
    if (r.role > 1) { r.outcome = outcome_role; return; }
    const U32 painted = paint_stack();
    U32 bytes = 0, crc = 0;
    const bool committed = r.role == 0 ? role0_menu_boot(bytes, crc) : role1_menu_boot(bytes);
    const bool serve = r.role == 0 && !r.menu.refused;
    if (r.role == 1 && committed) {
        crc = r.image_crc;                             // the transfer's CRC, checked at its last sector
        r.image_bytes = bytes;
    }
    r.committed = committed;
    if (committed) {
        if (hand_off(bytes, crc)) { xy_forever_storage_image_loaded = 1; r.outcome = outcome_file; }
        else r.outcome = outcome_copy;
    } else {
        r.outcome = r.declined ? (r.role == 0 && !serve ? outcome_no_image : outcome_refused) : outcome_link;
    }
    r.stack_used = stack_used(painted);
    r.end_ms = now();
#if defined(XYF_USB_SERVICE)
    // Dry-run boot (usb_service.hpp): everything up to the OS start, then back
    // to the USB service with this result (b), on both processors.
    if (usb_service::dry_run_requested()) usb_service::dry_run_finish(&r, sizeof(r));
#endif
}

#if defined(XYF_NO_EMBEDDED)
// No library image was loaded and there is no embedded OS (project rule):
// B says why on screen; cold.cpp then halts both processors until a power cycle.
extern "C" void xy_forever_no_image() {
    using namespace xy_forever::file_boot;
    if (result.role != 1) return;
    const U32 o = result.outcome;
    xy_forever::key_ui::service_screen(
        o == outcome_link ? "Internal link lost." : o == outcome_copy ? "Firmware copy failed." :
                            "No firmware could be started.",
        "Turn the OP-XY off and on.", "", "");
}
#endif
