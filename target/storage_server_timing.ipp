// Part of storage_server.cpp, included there at this point (file scope, after serve()): not compiled alone.
#include "xyf_clock.hpp"
// #144: where A's time goes in B's file requests. A counts the requests it serves by kind (read 0x2a, write 0x2b,
// every other) with the cycles serve() took for each (the stock executor's storage work, its reply queued; a read's
// data body leaves after that, by DMA), and the gap since the previous request ended (A waiting for B: the link
// both ways and B's own work; only gaps under gap_bound, so an idle link between transfers is not counted). Sums
// in units of 1024 cycles (2.56 us), longest in cycles. B reads them with path-class access requests tagged
// file_engine::timing_tag (file_client::a_timing), answered here before the engine: nothing reaches storage.
// The other requests also count by operation (file_engine::TimingSlot), so a small file's open, close, rename
// pair and mkdirs show apart; a request that opened the read-write window counts as t_window_open instead. Each
// also sums the cycles of A's own checks inside it (a_checks: storage_server_marker.ipp), apart from the executor.
namespace {
struct ATiming {
    U32 reads, read_units, read_max, writes, write_units, write_max;
    U32 others, other_units, gaps, gap_units, gap_max, checks;   // checks: how many (a_checks)
    U32 ops[file_engine::timing_op_slots][4];                // count, sum (units), longest (cycles), checks (units)
};
static_assert(sizeof(ATiming) == 4 * file_engine::timing_words, "A's timing words");
ATiming a_timing __attribute__((section(".data"))) = {};
uint64_t a_last_end __attribute__((section(".data"))) = 0;
const uint64_t gap_bound = 20u * 400000u;                    // 20 ms
U32 units(uint64_t cycles) { return U32(cycles >> 10); }
U32 longest(U32 was, uint64_t cycles) { return cycles > was ? (cycles >> 32 ? 0xffffffffu : U32(cycles)) : was; }
#if defined(XYF_MTP) || defined(XYF_USB_FILE_WRITE)
bool window_now() { return window_open != 0; }
#else
bool window_now() { return false; }
#endif
#include "storage_server_tfw_probe.ipp"
U32 op_slot(U32 op, U32 argument) {                          // file_client's op numbers
    using namespace file_engine;
    switch (op) {
    case 0x08: return t_mount;
    case 0x09: return t_unmount;
    case 0x21: return argument == rename_from ? t_rename_from :
                      argument == rename_to || argument == rename_over || argument == rename_upload ? t_rename_to :
                      t_access;
    case 0x22: return t_stat;
    case 0x23: return t_statfs;
    case 0x25: return t_unlink;
    case 0x26: return t_mkdir;
    case 0x27: return t_rmdir;
    case 0x29: return t_open;
    case 0x2c: return t_seek;
    case 0x2d: return t_close;
    case 0x2e: return t_opendir;
    case 0x2f: return t_readdir;
    case 0x30: return t_closedir;
    default: return t_other;
    }
}
bool timing_query(U32 request, Stock121PeerSession& session) {
    const U32 argument = word(request + 8);
    if (word(request) != file_engine::path_class || byte(request + 5) != 0x21u ||
        (argument & ~0xffu) != file_engine::timing_tag)
        return false;
#if defined(XYF_USB_FILE_WRITE)
    rename_armed = 0;                                        // as any request: a rename pairs only adjacent ones
#endif
    U32* w = reinterpret_cast<U32*>(&a_timing);
    const U32 k = argument & 0xffu;
    if (k == file_engine::timing_clear) {
        for (U32 i = 0; i != file_engine::timing_words; ++i) w[i] = 0;
        a_last_end = 0; a_checks.count = 0;
        session.reply(request, 0, 0);
    } else if (2 * k + 1 < file_engine::timing_words) session.reply(request, w[2 * k], w[2 * k + 1]);
    else session.reply(request, 0xffffffffu, error_access);
    return true;
}
// The serve hook: serve() timed.
void timed_serve(void* context, U32 request, Stock121PeerSession& session) {
    const uint64_t start = xyf::clock::cycles();
    if (tfw_probe_query(context, request, session)) return;
    if (boot_probe_query(request, session)) return;
    if (timing_query(request, session)) return;
    const U32 vtable = word(request), op = byte(request + 5), argument = word(request + 8);
    const bool had_window = window_now();
    const uint64_t checked = a_checks.cycles;
    serve(context, request, session);
    const uint64_t end = xyf::clock::cycles(), took = end - start;
    ATiming& t = a_timing;
    if (a_last_end && start - a_last_end < gap_bound) {
        ++t.gaps; t.gap_units += units(start - a_last_end); t.gap_max = longest(t.gap_max, start - a_last_end);
    }
    a_last_end = end;
    if (vtable == file_engine::read_class && op == 0x2au) {
        ++t.reads; t.read_units += units(took); t.read_max = longest(t.read_max, took);
    } else if (vtable == file_engine::write_class && op == 0x2bu) {
        ++t.writes; t.write_units += units(took); t.write_max = longest(t.write_max, took);
    } else {
        ++t.others; t.other_units += units(took);
        const bool opened = !had_window && window_now();
        U32* s = t.ops[opened ? U32(file_engine::t_window_open) : op_slot(op, argument)];
        ++s[0]; s[1] += units(took); s[2] = longest(s[2], took); s[3] += units(a_checks.cycles - checked);
    }
    t.checks = a_checks.count;
}
}
#if defined(XYF_MTP) || defined(XYF_USB_FILE_WRITE)
namespace xy_forever { namespace storage_server {
uint32_t engine_identity() {
    const CheckSpan span;
    uint32_t bits = 0;
    for (U32 i = 0; i != xyf::mtp_spans::count; ++i) {
        const uint32_t* s = xyf::mtp_spans::table[i];
        if (crc(s[0], s[1]) != s[2]) bits |= 1u << (i & 31u);
    }
    return bits;
}
volatile U32 spans_matched __attribute__((section(".data"))) = 0;
uint64_t spans_matched_at __attribute__((section(".data"))) = 0;
bool spans_match() {
    const uint64_t now = xyf::clock::cycles();
    if (spans_matched && now - spans_matched_at < uint64_t(spans_recheck_ms) * 400000u) return true;   // 400 MHz
    spans_matched = engine_identity() == 0 ? 1u : 0u;
    spans_matched_at = now;
    return spans_matched != 0;
}
} }
#endif
