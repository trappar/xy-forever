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

// b: link state shared with the key_ui hooks (initialized data, not BSS).
Stock121PeerSession* volatile b_session __attribute__((section(".data"))) = 0;
volatile U32 b_lost __attribute__((section(".data"))) = 0;
volatile U32 b_last_send __attribute__((section(".data"))) = 0;
volatile U32 b_sequence __attribute__((section(".data"))) = 0;
volatile U32 b_choice __attribute__((section(".data"))) = 0;      // 0 none, 1 default, 2 entry
volatile U32 b_entry __attribute__((section(".data"))) = 0;
#if defined(XYF_ONE_SESSION)
volatile U32 b_patience_ms __attribute__((section(".data"))) = 0;   // Test 065
volatile U32 b_keepalives __attribute__((section(".data"))) = 0;
#endif
volatile U32 b_catalog_ok __attribute__((section(".data"))) = 0;
#if defined(XYF_ONE_SESSION)
#define XYF_A_STORAGE_WORD a_storage_word
// 191: B's catalog request in flight while the menu runs (0 none, 1 asked, 2 the blob), since when,
// the echo count before it, and when A's last answer said its mount failed (0: it did not).
volatile U32 b_fetching __attribute__((section(".data"))) = 0;
volatile U32 b_fetch_at __attribute__((section(".data"))) = 0;
volatile U32 b_fetch_echoes __attribute__((section(".data"))) = 0;
volatile U32 b_failed_at __attribute__((section(".data"))) = 0;
#else
#define XYF_A_STORAGE_WORD 0u
#endif

xyf::catalog::Catalog& b_catalog() { return *reinterpret_cast<xyf::catalog::Catalog*>(xyf_boot::library_buffer); }
bool running(SessionResult s) { return s == SessionResult::waiting || s == SessionResult::choose_image; }

// b: one bounded poll; false (and lost) once the session left the held state.
bool b_step(Stock121PeerSession& session) {
    if (b_lost) return false;
    const SessionResult status = session.poll(now());
    ++result.polls;
    result.poll_result = U32(status);
    if (running(status)) return true;
    b_lost = 1;
    return false;
}
// b: wait (bounded) for the outstanding echo. A file request still out past its caller's
// bound is not (yet) a lost link: the caller fails (link), the session stays up until lost_ms.
bool b_settle(Stock121PeerSession& session) {
    const U32 start = now();
    while (session.busy() && !b_lost && now() - start < answer_ms) b_step(session);
    if (session.busy() && !session.filing()) b_lost = 1;
    return !b_lost && !session.busy();
}
// b: one command as a request (Stock121PeerSession::ask). a's loop answers it, so it
// waits in a's request slot while a is busy (mount, scan, image read) and cannot be
// lost (Diagnosis 075: the op-10 command before this was overwritten by b's next
// header while a was still mounting). Bounded by wait_ms.
bool b_ask(Stock121PeerSession& session, U32 value, U32 wait_ms) {
    if (!b_settle(session)) return false;              // an outstanding keepalive echo first
#if defined(XYF_ONE_SESSION)
    // 191: A answers its first command after its mount (a full yaffs scan may outlast command_ms).
    if (!result.storage_ms && wait_ms < xyf_boot::start_ms) wait_ms = xyf_boot::start_ms;
#endif
    const U32 before = session.echoes();
    if (session.ask(value) != SessionResult::waiting) { b_lost = 1; return false; }
    ++result.menu.commands;
    const U32 start = now();
    while (session.busy() && !b_lost && now() - start < wait_ms) {
        b_step(session);
#if defined(XYF_ONE_SESSION)
        usb_service::guard_reload();                    // a guarded home never resets while a is busy
#endif
    }
    if (session.busy() || session.echoes() != before + 1) b_lost = 1;
    // Report (b): when a answered the first command, and the longest wait for an answer.
    const U32 waited = now() - start;
    if (!b_lost && !result.storage_ms) result.storage_ms = now();
    if (waited > result.hash_ms) result.hash_ms = waited;
    b_last_send = now();
    return !b_lost;
}
// b: one tagged command item.
bool b_send(Stock121PeerSession& session, U32 item, U32 wait_ms = xyf_boot::command_ms) {
#if defined(XYF_ONE_SESSION)
    usb_service::rpc_reset();                          // Test 065: a leaves RPC mode on every b_send item
#endif
    // The low three bits stay 0 (a's bootloader's select takes the argument & 7: the user area).
    return b_ask(session, Stock121PeerSession::command_tag << 16 | (item & 0xffu) << 8 | (++b_sequence << 3 & 0xf8u),
                 wait_ms);
}

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

// b: fetch, accept, copy and prepare stage 2 (bounded by stage2_ms).
__attribute__((noinline)) void b_load_stage2(Stock121PeerSession& session) {
    xyf_boot::Stage2Info& s = result.stage2;
    s.status = xyf_boot::s2_link;
    if (!b_send(session, xyf_boot::item_stage2)) return;          // b_lost already set
    if (session.transfer(reinterpret_cast<Byte*>(staging), staging_bytes, true) != SessionResult::waiting) {
        b_lost = 1;                                     // a holds a frozen blob nobody fetches: give up
        return;
    }
    const U32 start = now();
    while (!session.blob_done() && now() - start < xyf_boot::stage2_ms && b_step(session)) {}
    if (!session.blob_done()) { b_lost = 1; return; }
    b_last_send = now();
    const U32 size = session.image_size();
    s.file_bytes = size;
    xyf::stage2::Header header;
    uint32_t detail = 0;
    const xyf::stage2::Blob blob = xyf::stage2::classify(reinterpret_cast<const Byte*>(staging), size,
                                                         xyf::stage2::this_stage1, header, detail);
    s.check = detail;                                   // a's note (refused) or b's Check
    if (blob == xyf::stage2::Blob::refused) { s.status = xyf_boot::s2_refused; return; }
    if (blob != xyf::stage2::Blob::ready) { s.status = xyf_boot::s2_check; return; }
    const Byte* file = reinterpret_cast<const Byte*>(staging);
    s.version = header.version; s.entry = header.entry;
    volatile Byte* to = reinterpret_cast<volatile Byte*>(header.load_address);
    for (U32 i = 0; i != header.image_bytes; ++i) to[i] = file[xyf::stage2::header_bytes + i];
    if (crc32(to, header.image_bytes) != header.image_crc32) { s.status = xyf_boot::s2_copy; return; }
    prepare_code(header.load_address, header.load_address + header.image_bytes);
    s.status = xyf_boot::s2_ready;
    s.load_ms = now();
    b_stage2_entry = header.entry;
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
// a follows b. b's bootloader asks (F1), or b went silent past the session's
// idle window: close storage (the read-write window first) and restart, so a's bootloader
// serves b's next boot (it answers the request the session holds). Never between a request's
// executor and its answer: only from this loop, between polls.
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
bool role0_menu_boot(U32& bytes, U32& crc) {
    Result& r = result;
    Stock121PeerSession session(r.diagnostic);         // zeroes the diagnostic
#if defined(XYF_MENU_LINK)
    session.stage(product_role0_idle_ms, handshake_ms);
#else
    session.stage(role0_idle_ms, handshake_ms);
#endif
#if defined(XYF_ONE_SESSION)
    // Test 065: one link session per boot. The storage server answers b's file requests in
    // this session (RPC mode) on the same storage session the library scan and boot use.
    cold_storage::Stock121ReadOnlySession storage(r.storage);
    storage_server::install(session, storage);
    SessionResult status = session.begin(services::Phase::cold_after_teardown,
        reinterpret_cast<Byte*>(scratch), scratch_bytes, 0, 0, now(), xyf_boot::role0_cap_ms);
    r.begin_result = U32(status);
#else
    SessionResult status = session.begin(services::Phase::cold_after_teardown,
        reinterpret_cast<Byte*>(scratch), scratch_bytes, 0, 0, now(), xyf_boot::role0_cap_ms);
    r.begin_result = U32(status);
    // Storage nests inside the link session: it unmounts before the session closes.
    cold_storage::Stock121ReadOnlySession storage(r.storage);
#endif
    bool mounted = false;
    xyf::library::Library& lib = *reinterpret_cast<xyf::library::Library*>(xyf_boot::library_buffer);
    xyf::library::detail::reset(lib);
    U32 catalog_bytes = 0;
    if (running(status)) {
#if defined(XYF_MENU_ON_FAILURE)
        r.timing.a_mount_start_ms = now();
#endif
#if defined(XYF_ONE_SESSION)
        const U32 mount_start = now();
#endif
        r.storage_begin = U32(storage.begin_fresh(services::Phase::cold_after_teardown));
#if defined(XYF_MENU_ON_FAILURE)
        r.timing.a_mount_ms = now() - r.timing.a_mount_start_ms;
        r.timing.valid |= xyf_boot::timing_mount;
        const U32 scan_start = now();
#endif
        mounted = r.storage_begin == U32(cold_storage::Result::ok);
#if defined(XYF_ONE_SESSION)
        // Test 065: the server shares this mount. 191: also when it failed (op 8 answers a failure
        // until a retry mounts; never a second begin_fresh of its own).
        storage_server::adopt();
        a_note_mount(storage, r.storage_begin, now() - mount_start);
#endif
        const bool ready = mounted && storage.direct_ready();
        if (ready) {
            const xyf::library::Error error = xyf::library::scan_direct(storage, lib,
                reinterpret_cast<Byte*>(xyf_boot::scan_buffer), xyf_boot::scan_capacity);
            r.menu.scan_error = U32(error);
            if (error != xyf::library::Error::ok) xyf::library::detail::reset(lib);
        } else r.menu.scan_error = 0xffu;
        r.library.count = lib.count; r.library.skipped = lib.skipped;
        catalog_bytes = xyf::catalog::encode(lib, reinterpret_cast<Byte*>(xyf_boot::catalog_buffer),
                                             xyf_boot::catalog_capacity, XYF_A_STORAGE_WORD);
        r.menu.catalog_bytes = catalog_bytes;
        r.storage_ms = now();
#if defined(XYF_MENU_ON_FAILURE)
        r.timing.a_scan_ms = now() - scan_start;
        r.timing.a_ready_ms = r.storage_ms;
        r.timing.valid |= xyf_boot::timing_scan;
#endif
        session.touch(now());
    }
    U32 handled = 0;
    while (running(status)) {
#if defined(XYF_ONE_SESSION)
        if (session.follow_now(now())) a_follow(storage);   // F1: b's bootloader asks
#endif
#if defined(XYF_ONE_SESSION) && (defined(XYF_MTP) || defined(XYF_USB_FILE_WRITE))
        // 192: B took the catalog whose word says "repair pending" and asks again at once: repair
        // before that ask is answered, so B waits for it as for the first mount (its menu runs).
        if (a_repair == xyf_boot::repair_pending && a_failure_served && !mounted && !session.blob_pending()) {
            a_failure_served = 0;
            mounted = a_repair_mount(storage);
            catalog_bytes = a_rescan(storage, mounted, lib);
            session.touch(now());
        }
#endif
#if defined(XYF_ONE_SESSION) && defined(XYF_USB_FILE_WRITE)
        // The write window closes after its idle time; a library it changed is scanned again
        // (b fetches the new catalog after it closed the window itself, or at its next catalog item).
        storage_server::tick(storage, now());
        if (storage_server::library_changed()) { catalog_bytes = a_rescan(storage, mounted, lib); session.touch(now()); }
#endif
        if (session.agreed()) {                         // unmount first, then let it close
            if (mounted) { a_close_files(storage); r.storage_end = U32(storage.end()); mounted = false; }
            session.allow_close();
        }
        status = session.poll(now());
        ++r.polls;
        if (!r.link_ms && r.diagnostic.link_initialized) r.link_ms = now();
        if (!running(status) || session.commands_seen() == handled) continue;
        handled = session.commands_seen();
        const U32 command = session.last_command(), item = (command >> 8) & 0xffu;
        r.menu.commands_seen = handled; r.menu.last_command = command;
#if defined(XYF_ONE_SESSION) && defined(XYF_USB_FILE_WRITE)
        // Every command item but reads, writes and the USB/guard switches closes the write window
        // first (catalog, check, boot, set default, install, dry run, reboot, run entry).
        if (item != storage_server::command_item && item != storage_server::file_write_item &&
            item != storage_server::usb_mode_item && item != storage_server::guard_item) {
            storage_server::end_writes(storage);
            if (storage_server::library_changed()) catalog_bytes = a_rescan(storage, mounted, lib);
        }
#endif
#if defined(XYF_ONE_SESSION)
        // Test 065: file-access items switch to RPC mode (the storage server latches its own
        // read / install / write modes from the same items); transfer items switch back.
        if (item == storage_server::file_write_item) a_prepared = 0xffffffffu;
        if (item == storage_server::command_item || item == storage_server::install_item ||
            item == storage_server::file_write_item) { session.set_rpc(true); continue; }
        if (item == xyf_boot::item_catalog || item == xyf_boot::item_check_default ||
            item == xyf_boot::item_boot_default ||
            (item >= xyf_boot::item_check_entry && item < xyf_boot::item_check_entry + xyf::library::max_entries) ||
            (item >= xyf_boot::item_boot_entry && item < xyf_boot::item_boot_entry + xyf::library::max_entries))
            session.set_rpc(false);
        // b reboots (target chosen by b's marker).
        if (item == storage_server::reboot_item) {
            a_answer_out(session);
            storage_server::finish(storage);
            if (mounted) { a_close_files(storage); r.storage_end = U32(storage.end()); mounted = false; }
            usb_service::reset_self();
        }
        if (item == storage_server::dry_run_item) { usb_service::a_dry_run(); continue; }
#if defined(XYF_MTP)
        if (item == storage_server::engine_run_item) {  // the engine's run entry until b's leave item (097)
            a_prepared = 0xffffffffu;
            session.set_rpc(true);                      // b's stock file requests reach the serve hook
            storage_server::run_engine(session, storage);
            handled = session.commands_seen();          // the leave item was consumed in run_engine
            // 139: the run entry may have changed the library: rescan before b's next command (b asks
            // for the catalog right after the leave item and returns to its menu).
            catalog_bytes = a_rescan(storage, mounted, lib);
            session.touch(now());
            continue;
        }
#endif
        if (xyf::state_write::collect(a_collector, command)) {
            a_prepared = 0xffffffffu;
            usb_service::a_set_default(storage, a_collector.hash);
            // Refresh even after a failed write: the newer slot may have reached
            // storage before a later verification/remount failure was reported.
            xyf::library::detail::reset(lib);
            r.menu.scan_error = 0xffu;
            if (mounted && storage.direct_ready()) {
                const xyf::library::Error error = xyf::library::scan_direct(storage, lib,
                    reinterpret_cast<Byte*>(xyf_boot::scan_buffer), xyf_boot::scan_capacity);
                r.menu.scan_error = U32(error);
                if (error != xyf::library::Error::ok) xyf::library::detail::reset(lib);
            }
            r.library.count = lib.count; r.library.skipped = lib.skipped;
            catalog_bytes = xyf::catalog::encode(lib, reinterpret_cast<Byte*>(xyf_boot::catalog_buffer),
                                                 xyf_boot::catalog_capacity, XYF_A_STORAGE_WORD);
            r.menu.catalog_bytes = catalog_bytes;
            session.touch(now());
            continue;
        }
        if (item == storage_server::usb_mode_item) continue;
#endif
        if (item == xyf_boot::item_catalog) {
#if defined(XYF_ONE_SESSION)
            if (!mounted && a_failure_served) {         // 191: B saw A's mount fail and asks again
                a_failure_served = 0;
                mounted = a_retry_mount(storage);
                catalog_bytes = a_rescan(storage, mounted, lib);
                session.touch(now());
            }
#endif
            if (session.freeze_blob(reinterpret_cast<const Byte*>(xyf_boot::catalog_buffer), catalog_bytes) ==
                SessionResult::ok) r.menu.catalog_ok = 1;
#if defined(XYF_ONE_SESSION)
            if (!mounted) a_failure_served = 1;
#endif
            continue;
        }
#if defined(XYF_STAGE2)
        if (item == xyf_boot::item_stage2) {
            a_serve_stage2(session, storage, mounted);
            continue;
        }
#endif
#if defined(XYF_STATE_WRITE)
        if (item >= xyf::state_write::item_set_default &&
            item < xyf::state_write::item_set_default + xyf::library::max_entries) {
            // The only storage write: next A/B state slot, then rescan so the
            // next catalog shows the result. The image boot path is untouched.
            using namespace xyf::state_write;
            U32 status = status_not_mounted, seq = 0;
            services::U32 step = 0;
            U32 write_result = 0;
            if (mounted && storage.direct_ready()) {
                Plan& plan = *reinterpret_cast<Plan*>(xyf_boot::plan_buffer);
                if (!plan_default(lib, item - item_set_default, plan)) status = status_plan;
                else {
                    seq = plan.seq;
                    const cold_storage::Result w = storage.write_state_slot(plan.path,
                        reinterpret_cast<const Byte*>(plan.text), plan.length,
                        reinterpret_cast<Byte*>(xyf_boot::readback_buffer), step);
                    write_result = U32(w);
                    status = w == cold_storage::Result::ok ? status_ok : status_storage;
                }
                if (storage.direct_ready()) {
                    const xyf::library::Error error = xyf::library::scan_direct(storage, lib,
                        reinterpret_cast<Byte*>(xyf_boot::scan_buffer), xyf_boot::scan_capacity);
                    if (error != xyf::library::Error::ok) xyf::library::detail::reset(lib);
                    catalog_bytes = xyf::catalog::encode(lib, reinterpret_cast<Byte*>(xyf_boot::catalog_buffer),
                                                         xyf_boot::catalog_capacity);
                }   // else: remount failed; boot commands refuse, end() still runs below
            }
            r.menu.state_write = status | (write_result & 0xffu) << 8 | (step & 0xffu) << 16 | (seq & 0xffu) << 24;
            session.touch(now());
            continue;
        }
#endif
#if defined(XYF_MENU_ON_FAILURE)
        // Test 059: CHECK (default or entry) reads, matches and admits the image while
        // storage stays mounted, and answers with a status blob; the following BOOT of the
        // same index unmounts and serves the staged image. A failed check leaves the
        // session and the mount open, so b can show the menu and try another entry.
        const bool check = item == xyf_boot::item_check_default ||
            (item >= xyf_boot::item_check_entry && item < xyf_boot::item_check_entry + xyf::library::max_entries);
        const bool boot = item == xyf_boot::item_boot_default ||
            (item >= xyf_boot::item_boot_entry && item < xyf_boot::item_boot_entry + xyf::library::max_entries);
        if (!check && !boot) continue;
        // Test 061: a blob (catalog / check status) still being restored must finish
        // before the next freeze (bounded; harmless when nothing is pending).
        { const U32 t = now(); while (session.blob_pending() && now() - t < 2000u) session.poll(now()); }
        U32 index = check ? item - xyf_boot::item_check_entry : item - xyf_boot::item_boot_entry;
        if (item == xyf_boot::item_check_default || item == xyf_boot::item_boot_default) index = a_default_index(lib);
        if (check) {
            U32 reason = a_prepare(storage, mounted, lib, index, bytes, crc);
            if (reason == reason_no_library) reason = a_content_reason(lib, index);   // the menu says why
            a_prepared = reason == reason_ok ? index : 0xffffffffu;
            xyf_boot::CheckStatus& s = *reinterpret_cast<xyf_boot::CheckStatus*>(xyf_boot::status_buffer);
            for (U32 i = 0; i != sizeof(s); ++i) reinterpret_cast<volatile Byte*>(&s)[i] = 0;
            s.magic = xyf_boot::check_magic; s.reason = reason; s.index = index;
            s.load_reason = r.load_reason; s.admit_reason = r.admit_reason;
            s.timing = r.timing;
            if (index < lib.count)
                for (U32 i = 0; i != 31 && lib.entries[index].id[i]; ++i) s.id[i] = lib.entries[index].id[i];
            session.freeze_blob(reinterpret_cast<const Byte*>(xyf_boot::status_buffer), sizeof(s));
            session.touch(now());
            continue;
        }
        U32 reason = a_prepared == index ? U32(reason_ok) : a_prepare(storage, mounted, lib, index, bytes, crc);
        a_prepared = 0xffffffffu;
        if (mounted) { a_close_files(storage); r.storage_end = U32(storage.end()); mounted = false; }
        r.storage_safe = storage.safe_to_handoff() && r.storage_end == U32(cold_storage::Result::ok);
        if (reason == reason_ok && !r.storage_safe) reason = reason_unmount;
        r.load_reason = reason == reason_ok ? r.load_reason : reason;
        xyf_core::hash::Digest digest;
        bool serve = false;
        if (reason == reason_ok) {
            // crc: a_prepare's CRC of exactly these staged bytes (checked against info.txt).
            const U32 freeze_start = now();
            serve = session.freeze_image(reinterpret_cast<const Byte*>(staging), bytes, crc, digest) == SessionResult::ok;
            r.timing.a_freeze_ms = now() - freeze_start; r.timing.valid |= xyf_boot::timing_freeze;
            if (serve) {
                for (U32 i = 0; i != 8; ++i)
                    r.digest[i] = digest.bytes[4 * i] | U32(digest.bytes[4 * i + 1]) << 8 |
                                  U32(digest.bytes[4 * i + 2]) << 16 | U32(digest.bytes[4 * i + 3]) << 24;
                r.image_bytes = bytes; r.image_crc = crc;
            }
        }
#else
        if (item != xyf_boot::item_boot_default &&
            (item < xyf_boot::item_boot_entry || item >= xyf_boot::item_boot_entry + xyf::library::max_entries)) continue;
        // Boot: pick, read, check against info.txt, unmount, admit, hash, freeze.
        U32 index = item - xyf_boot::item_boot_entry;
        if (item == xyf_boot::item_boot_default) {
            lib.state.once[0] = 0;                      // no "once" while read-only
            uint32_t choice = 0;
            index = xyf::library::boot_choice(lib, choice) == xyf::library::Choice::menu ? 0xffu : U32(choice);
        }
        r.library.index = index;
        xyf::cold_library::Selection selection;
        U32 reason = reason_no_library;
        if (mounted && storage.direct_ready() && entry_selection(lib, index, selection)) {
            const char* id = lib.entries[index].id;
            for (U32 i = 0; i != 31 && id[i]; ++i) r.library.id[i] = id[i];
            r.library.expected_bytes = selection.bytes; r.library.expected_crc = selection.crc32;
            r.library.flags = (selection.has_bytes ? 1u : 0u) | (selection.has_crc32 ? 2u : 0u);
            reason = read_image(storage, selection.path, bytes);
            if (reason == reason_ok) {
                crc = crc32(reinterpret_cast<const volatile Byte*>(staging), bytes);
                const bool matched = xyf::cold_library::matches(selection, bytes, crc);
                r.library.flags |= matched ? 4u : 0u;
                if (!matched) reason = reason_mismatch;
            }
        }
        if (mounted) { a_close_files(storage); r.storage_end = U32(storage.end()); mounted = false; }
        r.storage_safe = storage.safe_to_handoff() && r.storage_end == U32(cold_storage::Result::ok);
        if (reason == reason_ok && !r.storage_safe) reason = reason_unmount;
        r.load_reason = reason;
        xyf_core::hash::Digest digest;
        bool serve = false;
        if (reason == reason_ok) {
            r.admit_reason = admit(reinterpret_cast<Byte*>(staging), bytes);
            if (r.admit_reason == reason_ok) {
                serve = session.freeze_image(reinterpret_cast<const Byte*>(staging), bytes, crc, digest) == SessionResult::ok;
                if (serve) {
                    for (U32 i = 0; i != 8; ++i)
                        r.digest[i] = digest.bytes[4 * i] | U32(digest.bytes[4 * i + 1]) << 8 |
                                      U32(digest.bytes[4 * i + 2]) << 16 | U32(digest.bytes[4 * i + 3]) << 24;
                    r.image_bytes = bytes; r.image_crc = crc;
                }
            }
        }
#endif
        if (!serve) { session.refuse(); r.menu.refused = 1; }
        r.hash_ms = now();
        session.touch(now());
    }
#if defined(XYF_ONE_SESSION)
    // b went silent past the idle window after it spoke (hung, restarted without reaching this
    // session, or one of its requests was lost) with nothing frozen or agreed: restart instead of
    // halting, so a reset b's bootloader gets past its boot-A step.
    if (status == SessionResult::timeout && session.commands_seen() && !session.agreed() && !session.committed() &&
        !session.image_size()) a_follow(storage);
#endif
    if (mounted) { a_close_files(storage); r.storage_end = U32(storage.end()); mounted = false; }
    r.poll_result = U32(status);
    if (status != SessionResult::ready) r.end_result = U32(session.end());
    r.declined = session.declined();
    r.menu.blobs = session.blobs();
    return session.committed();
}

#if defined(XYF_MENU_ON_FAILURE)
// Test 059 (b): menu-on-failure state and helpers.
volatile U32 b_failed __attribute__((section(".data"))) = 0;   // a check failed: the menu waits (no idle timeout)
char b_notice[64] __attribute__((section(".data"))) = {0};
const xyf_boot::CheckStatus& b_status() { return *reinterpret_cast<const xyf_boot::CheckStatus*>(xyf_boot::b_status_buffer); }

const char* reason_text(U32 reason) {
    return reason == reason_no_library ? "not in the library" : reason == reason_open ? "file missing" :
           reason == reason_size ? "too large" : reason == reason_read ? "read error" :
           reason == reason_mismatch ? "size/CRC differ from info.txt" :
           reason == reason_policy ? "image rejected" : reason == reason_unmount ? "storage error" :
           reason == reason_storage ? "storage error" : reason == reason_content ? "content not installed" :
           reason == reason_content_repair ? "content needs repair" : "can't boot";
}
void b_append(U32& n, const char* s) { while (*s && n + 1 < sizeof(b_notice)) b_notice[n++] = *s++; b_notice[n] = 0; }
// "Default opxy-125: file missing" / "opxy-125: image rejected".
void b_note(bool is_default, U32 reason) {
    U32 n = 0;
    if (is_default) b_append(n, "Default ");
    const char* id = b_status().magic == xyf_boot::check_magic && b_status().id[0] ? b_status().id : "entry";
    char safe[32];
    U32 k = 0;
    for (; k != 31 && id[k]; ++k) safe[k] = id[k];
    safe[k] = 0;
    b_append(n, safe);
    b_append(n, ": ");
    b_append(n, reason_text(reason));
}

// CHECK item: a answers a CheckStatus blob. Returns its reason (reason_storage and
// b_lost on a link failure, reason_read for a malformed answer).
U32 b_check(Stock121PeerSession& session, U32 item) {
    const U32 check_start = now();
    result.timing.valid = 0;                         // a failed new CHECK must not show old timings
    Byte* status = reinterpret_cast<Byte*>(xyf_boot::b_status_buffer);
    for (U32 i = 0; i != sizeof(xyf_boot::CheckStatus); ++i) status[i] = 0;
    if (!b_send(session, item) ||
        session.transfer(status, xyf_boot::b_status_capacity, true) != SessionResult::waiting) { b_lost = 1; return reason_storage; }
    const U32 start = now();
    while (!session.blob_done() && now() - start < xyf_boot::check_ms && b_step(session)) {}
    if (!session.blob_done()) { b_lost = 1; return reason_storage; }
    if (session.image_size() < sizeof(xyf_boot::CheckStatus) || b_status().magic != xyf_boot::check_magic)   // Test 062: >=
        return reason_read;
    if (b_status().timing.magic == xyf_boot::timing_magic && b_status().timing.version == 1)
        result.timing = b_status().timing;
    result.timing.b_check_ms = now() - check_start;
    result.timing.valid |= xyf_boot::timing_check;
    return b_status().reason;
}

// b: decode the catalog blob that arrived.
bool b_take_catalog(Stock121PeerSession& session) {
    Result& r = result;
    r.menu.catalog_bytes = session.image_size();
    b_catalog_ok = xyf::catalog::decode(reinterpret_cast<const Byte*>(xyf_boot::catalog_buffer),
                                        session.image_size(), b_catalog());
    r.menu.catalog_ok = b_catalog_ok;
    r.menu.catalog_entries = b_catalog_ok ? b_catalog().count : 0;
    r.menu.catalog_ms = now();
    return b_catalog_ok != 0;
}
bool b_fetch_catalog(Stock121PeerSession& session) {
    Result& r = result;
    if (!b_send(session, xyf_boot::item_catalog) ||
        session.transfer(reinterpret_cast<Byte*>(xyf_boot::catalog_buffer), xyf_boot::catalog_capacity, true) !=
            SessionResult::waiting) { b_lost = 1; return false; }
    const U32 start = now();
    while (!session.blob_done() && now() - start < xyf_boot::blob_ms && b_step(session)) {}
    if (!session.blob_done()) { b_lost = 1; return false; }
    return b_take_catalog(session);
}
#if defined(XYF_ONE_SESSION)
// 191: the catalog request without blocking the menu while A mounts (tick): asked, A answers after
// its first mount; then the blob in one loop (a retry's mount inside it), start_ms in all. The storage word tells B whether A's mount worked.
void b_fetch_begin(Stock121PeerSession& session) {
    xyf_boot::StorageStart& st = xyf_boot::storage_start;
    if (!b_settle(session)) return;
    usb_service::rpc_reset();
    b_fetch_echoes = session.echoes();
    if (session.ask(Stock121PeerSession::command_tag << 16 | (xyf_boot::item_catalog & 0xffu) << 8 |
                    (++b_sequence << 3 & 0xf8u)) != SessionResult::waiting) { b_lost = 1; st.phase = 4; return; }
    ++result.menu.commands; ++st.asks;
    b_fetching = 1; b_fetch_at = b_last_send = now();
    if (st.phase != 3) st.phase = 1;
}
// One step of it; true when it ended (b_catalog_ok, storage_start tell how).
bool b_fetch_step(Stock121PeerSession& session) {
    xyf_boot::StorageStart& st = xyf_boot::storage_start;
    usb_service::guard_reload();
    const bool alive = b_step(session);
    const U32 t = now(), waited = t - b_fetch_at;
    st.waited_ms = waited;
    if (waited > st.longest_ms) st.longest_ms = waited;
    if (alive && b_fetching == 1 && !session.busy()) {
        if (session.echoes() != b_fetch_echoes + 1 ||
            session.transfer(reinterpret_cast<Byte*>(xyf_boot::catalog_buffer), xyf_boot::catalog_capacity, true) !=
                SessionResult::waiting) b_lost = 1;
        else {
            // The blob in one bounded loop, as b_fetch_catalog (sim: polls spread over the menu loop
            // could end it with a protocol error). A retry's mount runs inside it (A answered first).
            b_fetching = 2;
            while (!session.blob_done() && now() - b_fetch_at <= xyf_boot::start_ms && b_step(session))
                usb_service::guard_reload();
        }
    }
    if (alive && !b_lost && b_fetching == 2 && session.blob_done()) {
        b_fetching = 0; st.waited_ms = 0;
        b_last_send = t;
        if (!result.storage_ms) result.storage_ms = t;
        if (!st.answered_ms) st.answered_ms = t;
        b_take_catalog(session);
        const U32 word = b_catalog_ok ? xyf::catalog::reserved(reinterpret_cast<const Byte*>(xyf_boot::catalog_buffer)) : 0u;
        st.a_word = word;
        const bool failed = !b_catalog_ok || xyf_boot::storage_failed(word);
        st.phase = failed ? 3 : 2;
        // 192: a failed repair is final this boot (A mounts no more): B stops asking.
        b_failed_at = failed && xyf_boot::storage_repair(word) != xyf_boot::repair_failed ? (t ? t : 1u) : 0u;
        // 192: A repairs now: ask again at once, so nothing else reaches A meanwhile (as at its first mount).
        if (failed && xyf_boot::storage_repair(word) == xyf_boot::repair_pending) b_fetch_begin(session);
        return true;
    }
    if (b_lost || waited > xyf_boot::start_ms) { b_lost = 1; b_fetching = 0; st.phase = 4; return true; }
    return false;
}
#endif
#endif

// b (role 1). Returns the commit decision; bytes is the received image size.
bool role1_menu_boot(U32& bytes) {
    Result& r = result;
    const U32 frame = 0x8807ce7eu;                     // stock key status reply (Test017)
    r.menu.play = byte(frame + 2) == 0x42 && byte(frame + 3) == 0x0a && (byte(frame + 4 + 51 / 8) >> (51 % 8) & 1u);
#if defined(XYF_USB_MENU)
    if (usb_service::menu_requested()) r.menu.play = 1;    // Test 058: the host asked for the menu
#endif
#if defined(XYF_ONE_SESSION)
    if (usb_service::home_begin()) r.menu.play = 1;    // Test 065: our REBOOT / dry-run return: home
#endif
    Stock121PeerSession session(r.diagnostic);
    session.stage(role0_idle_ms, handshake_ms);
    SessionResult status = session.begin(services::Phase::cold_after_teardown,
        reinterpret_cast<Byte*>(scratch), scratch_bytes, reinterpret_cast<Byte*>(staging), staging_bytes,
#if defined(XYF_MENU_ON_FAILURE)
        now(), xyf_boot::role1_menu_cap_ms);           // Test 059: a failed default opens the menu
#else
        now(), r.menu.play ? xyf_boot::role1_menu_cap_ms : xyf_boot::role1_boot_cap_ms);
#endif
    r.begin_result = U32(status);
    b_lost = running(status) ? 0 : 1;
    while (!b_lost && !session.link_ready()) b_step(session);     // bounded by handshake_ms
    if (!b_lost) r.link_ms = now();
#if defined(XYF_MENU_ON_FAILURE)
    // Test 059: check the default first; if it can't boot, open the menu (like a
    // BIOS) with the reason; every menu choice is checked before it boots, and a
    // failed choice returns to the menu with a message. Only a checked image boots.
    U32 boot_item = 0;
    if (!b_lost && !r.menu.play) {
#if defined(XYF_SERVICE_ON_MARKER_ONLY)
        usb_service::progress("Checking the default firmware...", "");
#endif
        const U32 reason = b_check(session, xyf_boot::item_check_default);
        if (reason == reason_ok) boot_item = xyf_boot::item_boot_default;
        else if (!b_lost) { b_failed = 1; b_note(true, reason); r.menu.play = 1; }
#if defined(XYF_SERVICE_ON_MARKER_ONLY)
        usb_service::progress(reason == reason_ok ? "Default firmware checked." : b_lost ? "Storage link lost." : b_notice,
                              reason == reason_ok ? "Booting it..." : "Opening the menu...");
#endif
        r.menu.refused = reason | b_status().index << 8 | (reason == reason_ok ? 0u : 0x10000u);
    }
#if defined(XYF_ONE_SESSION)
    if (!b_lost && !boot_item) {                    // Test 065: home (USB on, guard, marker)
        b_session = &session;                       // home_start sends the guard item through the link
        usb_service::home_start();
        b_session = 0;
    }
#endif
    while (!b_lost && !boot_item) {
#if defined(XYF_ONE_SESSION)
        if (!b_catalog_ok && !b_fetching) b_fetch_begin(session);   // 191: the menu opens at once (tick)
        if (b_lost) break;
#else
        if (!b_catalog_ok && !b_fetch_catalog(session)) break;
#endif
        b_session = &session;
        b_last_send = now();
        b_choice = 0;
        xy_forever_key_ui();                        // XY Forever menu; ticks keepalives
        b_session = 0;
        r.menu.menu_ms = now();
        if (b_lost || !b_choice) break;                 // link lost, or the menu ended without a choice
#if defined(XYF_ONE_SESSION)
        while (b_fetching && !b_lost) b_fetch_step(session);   // 191: a choice before A answered: its answer first
                                                         // (192: and the repair's, when that answer asked again)
        if (b_lost) break;
#endif
        r.menu.choice = b_choice; r.menu.choice_entry = b_entry;
        const U32 reason = b_check(session, b_choice == 2 ? xyf_boot::item_check_entry + b_entry :
                                                            xyf_boot::item_check_default);
        if (reason == reason_ok)
            boot_item = b_choice == 2 ? xyf_boot::item_boot_entry + b_entry : xyf_boot::item_boot_default;
        else if (!b_lost) { b_failed = 1; b_note(b_choice != 2, reason); }
    }
#if defined(XYF_USB_MENU)
    usb_service::menu_release();                       // Test 058: USB off before any boot
#endif
    if (!b_lost && boot_item) {
        if (b_send(session, boot_item) &&
            session.transfer(reinterpret_cast<Byte*>(staging), staging_bytes, false) == SessionResult::waiting) {
            status = SessionResult::waiting;
            r.menu.boot_ms = now();
            xyf_boot::TimingDetail& d = r.detail;
            U32 poll_start = r.menu.boot_ms, admitted_at = 0;
            while ((status == SessionResult::waiting || status == SessionResult::choose_image ||
                    status == SessionResult::admit_image) &&
                   now() - r.menu.boot_ms < xyf_boot::boot_transfer_ms) {   // Test 061: own bound
                if (!d.b_metadata_ms && session.image_size()) d.b_metadata_ms = now() - r.menu.boot_ms;
                if (status == SessionResult::admit_image) {
                    r.received_bytes = session.image_size();
                    r.image_crc = session.received_crc();          // checked over the received bytes
                    r.admit_ms = now();
                    d.b_data_ms = poll_start - r.menu.boot_ms;
                    d.b_last_poll_ms = r.admit_ms - poll_start;
                    r.timing.b_receive_ms = r.admit_ms - r.menu.boot_ms;
                    r.timing.valid |= xyf_boot::timing_receive;
                    r.admit_reason = admit(reinterpret_cast<Byte*>(staging), r.received_bytes);
                    d.b_policy_ms = now() - r.admit_ms;
                    if (r.admit_reason != reason_ok) break;            // not admitted: never release
                    const xyf_core::hash::Digest& received = session.received_digest();
                    for (U32 i = 0; i != 8; ++i)
                        r.digest[i] = received.bytes[4 * i] | U32(received.bytes[4 * i + 1]) << 8 |
                                      U32(received.bytes[4 * i + 2]) << 16 | U32(received.bytes[4 * i + 3]) << 24;
                    const U32 hash_start = now();
                    if (session.admit(received) != SessionResult::ok) break;
                    admitted_at = now();
                    d.b_admit_hash_ms = admitted_at - hash_start;
                    r.timing.b_admit_ms = admitted_at - r.admit_ms;
                    r.timing.valid |= xyf_boot::timing_b_admit;
                }
                poll_start = now();
                status = session.poll(poll_start);
                ++r.polls;
            }
            if (admitted_at) d.b_release_ms = now() - admitted_at;
        } else status = SessionResult::unavailable;
    } else status = SessionResult::unavailable;
#else
    if (!b_lost && r.menu.play) {
        if (b_send(session, xyf_boot::item_catalog) &&
            session.transfer(reinterpret_cast<Byte*>(xyf_boot::catalog_buffer), xyf_boot::catalog_capacity, true) ==
                SessionResult::waiting) {
            const U32 start = now();
            while (!session.blob_done() && now() - start < xyf_boot::blob_ms && b_step(session)) {}
            if (session.blob_done()) {
                r.menu.catalog_bytes = session.image_size();
                b_catalog_ok = xyf::catalog::decode(reinterpret_cast<const Byte*>(xyf_boot::catalog_buffer),
                                                    session.image_size(), b_catalog());
                r.menu.catalog_ok = b_catalog_ok;
                r.menu.catalog_entries = b_catalog_ok ? b_catalog().count : 0;
            } else b_lost = 1;                          // a request still outstanding: give up
        }
        r.menu.catalog_ms = now();
#if defined(XYF_STAGE2)
        if (!b_lost) b_load_stage2(session);
#endif
        if (!b_lost) {
            b_session = &session;
            b_last_send = now();
            xy_forever_key_ui();                    // XY Forever menu; ticks keepalives
            b_session = 0;
        }
        r.menu.menu_ms = now();
    }
    if (!b_lost) {
        r.menu.choice = b_choice ? b_choice : 1;
        r.menu.choice_entry = b_entry;
        const U32 item = b_choice == 2 ? xyf_boot::item_boot_entry + b_entry : xyf_boot::item_boot_default;
        if (b_send(session, item) &&
            session.transfer(reinterpret_cast<Byte*>(staging), staging_bytes, false) == SessionResult::waiting) {
            status = SessionResult::waiting;
            r.menu.boot_ms = now();
            while (status == SessionResult::waiting || status == SessionResult::choose_image ||
                   status == SessionResult::admit_image) {
                if (status == SessionResult::admit_image) {
                    r.received_bytes = session.image_size();
                    r.image_crc = session.received_crc();          // checked over the received bytes
                    r.admit_ms = now();
                    r.admit_reason = admit(reinterpret_cast<Byte*>(staging), r.received_bytes);
                    if (r.admit_reason != reason_ok) break;            // not admitted: never release
                    const xyf_core::hash::Digest& received = session.received_digest();
                    for (U32 i = 0; i != 8; ++i)
                        r.digest[i] = received.bytes[4 * i] | U32(received.bytes[4 * i + 1]) << 8 |
                                      U32(received.bytes[4 * i + 2]) << 16 | U32(received.bytes[4 * i + 3]) << 24;
                    if (session.admit(received) != SessionResult::ok) break;
                }
                status = session.poll(now());
                ++r.polls;
            }
        } else status = SessionResult::unavailable;
    } else status = SessionResult::unavailable;
#endif
    r.poll_result = U32(status);
    if (status != SessionResult::ready) r.end_result = U32(session.end());
    r.declined = session.declined();
    r.menu.blobs = session.blobs();
    bytes = session.image_size();
    return session.committed();
}
}


namespace xy_forever { namespace xyf_boot {
StorageStart storage_start __attribute__((section(".data"))) = {start_magic, 2, 0, 0, 0, 0, 0, 0};   // 192: version 2 (repair)
#if defined(XYF_ONE_SESSION)
// 191: a link user (a USB file command, a menu command) while the catalog request is out: its end
// first (A's answer is normally milliseconds away), at most fetch_wait_ms. True when none is out.
bool fetch_wait() {
    Stock121PeerSession* session = b_session;
    const U32 start = now(), phase = storage_start.phase;
    while (session && b_fetching && !b_lost && now() - start < fetch_wait_ms)
        if (b_fetch_step(*session) && !b_lost && (storage_start.phase != 3 || phase == 1)) key_ui::refresh_catalog();
    return !b_fetching;
}
#endif
bool tick() {
    Stock121PeerSession* session = b_session;
    if (!session || b_lost) return false;
#if defined(XYF_ONE_SESSION)
    // 191: A's first answer (its mount) or a retry after a failed mount, without blocking the menu.
    if (b_fetching) {
        const U32 phase = storage_start.phase;
        if (!b_fetch_step(*session)) return false;
        if (!b_lost && (storage_start.phase != 3 || phase == 1)) key_ui::refresh_catalog();
        return true;
    }
    if (b_failed_at && now() - b_failed_at >= retry_ms && !session->busy()) {
        b_fetch_begin(*session);
        return false;
    }
#endif
    const U32 before = b_lost;
    if (b_step(*session)) {
        const U32 t = now();
        if (session->busy()) {
#if defined(XYF_ONE_SESSION)
            // A file request left at link_sent(); it may stay out up to lost_ms.
            if (t - b_last_send > (session->filing() ? xyf_boot::lost_ms :
                                   b_patience_ms ? b_patience_ms : 2 * answer_ms)) b_lost = 1;
#else
            if (t - b_last_send > 2 * answer_ms) b_lost = 1;
#endif
        } else if (t - b_last_send >= keepalive_ms) {
            if (session->command(keepalive_tag << 16 | (++b_sequence & 0xffffu)) == SessionResult::waiting) {
                b_last_send = t;
#if defined(XYF_ONE_SESSION)
                ++b_keepalives;
#endif
            }
        }
    }
    return b_lost != before;
}
#if defined(XYF_ONE_SESSION)
bool link_value(uint32_t value) {
    Stock121PeerSession* session = b_session;
    return session && !b_lost && fetch_wait() && b_ask(*session, value, b_patience_ms ? b_patience_ms : xyf_boot::command_ms);
}
bool link_up() { return b_session && !b_lost; }
Stock121PeerSession* link_claim() {
    Stock121PeerSession* session = b_session;
    return session && !b_lost && fetch_wait() && b_settle(*session) ? session : 0;   // 191: after a catalog
}
bool link_step() {
    Stock121PeerSession* session = b_session;
    usb_service::guard_reload();                       // B's guard stays armed: kicked while it waits
    return session && b_step(*session);
}
void link_sent() { b_last_send = now(); }
// before any stock reset of b. A request still open is polled until
// answered or close_ms passes (wait: false in the guard interrupt, where nothing completes it);
// then the session ends: the stock master close 889f562e turns SPI and both DMA channels off and
// removes the link interrupts, so nothing can complete the request later (its object is the
// session's own, which outlives this: the session is the menu's caller's). The link is lost from
// here either way. True only when a fresh snapshot shows the hardware off (check_quiet).
bool link_close(bool wait) {
    Stock121PeerSession* session = b_session;
    if (session) {
        const U32 start = now();
        while (wait && session->busy() && now() - start < close_ms) {
            session->poll(now());
            usb_service::guard_reload();
        }
        b_lost = 1;
        result.end_result = U32(session->end());
    }
    peer_services::Snapshot s;
    return Stock121PeerSession::capture(s) && peer_services::check_quiet(s) == peer_services::Result::ok;
}
void link_patience(uint32_t ms) { b_patience_ms = ms; }
bool link_idle() { return b_session && !b_lost && !b_fetching && !b_session->busy(); }
uint32_t link_keepalives() { return b_keepalives; }
bool refresh_catalog() {
    if (b_fetching) return false;                       // 191: one is on its way (tick)
    b_catalog_ok = 0;
    return b_session && !b_lost && b_fetch_catalog(*b_session);
}
#endif
#if defined(XYF_ONE_SESSION)
// 193: the menu's view of A's storage (xyf_menu StorageView). A rescan after a write (answered before)
// keeps the library the menu shows; the first mount's first 400 ms stay blank (a normal mount answers in
// about 250 ms); past 2 s it is the full scan after an unclean shutdown (normal: 0.25 s, scan 4.5-17 s).
uint32_t storage_view() {
    namespace m = xyf::menu;
    const StorageStart& st = storage_start;
    if (b_lost || st.phase == 4) return m::storage_unavailable;
    if (st.phase == 3) {                                   // A's mount failed: B asks again, or repairs
        const uint32_t repair = storage_repair(st.a_word);
        return repair == repair_failed ? m::storage_unavailable : repair == repair_pending ? m::storage_repairing :
                                         m::storage_starting;
    }
    if (b_catalog_ok || st.answered_ms) return m::storage_ready;
    const U32 waited = b_fetching ? now() - b_fetch_at : 0u;
    return waited < 400u ? m::storage_waiting : waited < 2000u ? m::storage_starting : m::storage_checking;
}
#endif
const char* status() {
#if defined(XYF_USB_SERVICE) && defined(XYF_ONE_SESSION)
    if (const char* off = usb_service::usb_notice()) return off;    // 097: the menu came up without USB
#endif
#if defined(XYF_MENU_ON_FAILURE)
    if (!b_lost && b_notice[0]) return b_notice;      // Test 059: why the menu opened
#endif
#if defined(XYF_ONE_SESSION)
    if (b_lost) return "Internal link lost. Turn the OP-XY off and on.";   // the menu (USB) stays
    // 193: the first row says "Starting storage..." (xyf_menu); this line says why it takes long.
    switch (storage_view()) {
    case xyf::menu::storage_checking: return "Checking storage after an unclean shutdown";   // 191: a full scan
    case xyf::menu::storage_repairing: return "Repairing storage after an unclean shutdown";   // 192
    case xyf::menu::storage_starting: return storage_start.phase == 3 ? "Storage did not mount: trying again" : "";
    case xyf::menu::storage_unavailable: return "The firmware library can't be read, so nothing can boot.";
    default: return "";
    }
#endif
    return b_lost ? "Storage link lost: starting the built-in OP-XY." :   // nothing when storage is OK
           b_catalog_ok ? "" : "Storage link: OK (library unavailable)";
}
const char* command(uint32_t) { return "Not available yet."; }
bool link_lost() { return b_lost != 0; }
const xyf::catalog::Catalog* catalog() { return b_catalog_ok ? &b_catalog() : 0; }
void choose_default() { b_choice = 1; }
#if defined(XYF_ONE_SESSION)
uint32_t clock_ms() { return now(); }
#endif
void choose(uint32_t entry) { b_choice = 2; b_entry = entry; }
#if defined(XYF_MENU_ON_FAILURE)
bool menu_forced() { return b_failed != 0; }
#else
bool menu_forced() { return false; }
#endif
#if defined(XYF_STATE_WRITE)
const char* set_default(uint32_t entry) {
    Stock121PeerSession* session = b_session;
    if (!session || b_lost) return "Storage link lost.";
    if (!b_catalog_ok || entry >= b_catalog().count) return "Set default: no such entry.";
    if (!b_send(*session, xyf::state_write::item_set_default + entry)) return "Storage link lost.";
    // a writes, remounts and rescans before it answers this catalog command.
    if (!b_send(*session, xyf_boot::item_catalog, xyf_boot::write_ms) ||
        session->transfer(reinterpret_cast<Byte*>(xyf_boot::catalog_buffer), xyf_boot::catalog_capacity, true) !=
            SessionResult::waiting) { b_lost = 1; return "Storage link lost."; }
    const U32 start = now();
    while (!session->blob_done() && now() - start < xyf_boot::write_ms && b_step(*session)) {}
    if (!session->blob_done()) { b_lost = 1; return "Storage link lost."; }
    b_last_send = now();
    b_catalog_ok = xyf::catalog::decode(reinterpret_cast<const Byte*>(xyf_boot::catalog_buffer),
                                        session->image_size(), b_catalog());
    result.menu.catalog_entries = b_catalog_ok ? b_catalog().count : 0;
    if (!b_catalog_ok) return "Library unavailable after the write.";
    return b_catalog().default_index == entry ? "Default saved." : "Could not save the default.";
}
#endif
#if defined(XYF_STAGE2)
uint32_t stage2_entry() { return b_lost ? 0 : b_stage2_entry; }
void stage2_done(uint32_t exit, uint32_t run_ms) { result.stage2.exit = exit; result.stage2.run_ms = run_ms; }
#if defined(XYF_STAGE2_MENU)
const uint8_t* catalog_blob(uint32_t& bytes) {
    bytes = b_catalog_ok ? result.menu.catalog_bytes : 0;
    return bytes ? reinterpret_cast<const uint8_t*>(catalog_buffer) : 0;
}
#endif
#endif
} }

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
