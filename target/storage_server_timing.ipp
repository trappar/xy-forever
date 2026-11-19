// Part of storage_server.cpp, included there at this point (file scope, after serve()): not compiled alone.
#include "xyf_clock.hpp"
// #144: where A's time goes in B's file requests. A counts the requests it serves by kind (read 0x2a, write 0x2b,
// every other) with the cycles serve() took for each (the stock executor's storage work, its reply queued; a read's
// data body leaves after that, by DMA), and the gap since the previous request ended (A waiting for B: the link
// both ways and B's own work; only gaps under gap_bound, so an idle link between transfers is not counted). Sums
// in units of 1024 cycles (2.56 us), longest in cycles. B reads them with path-class access requests tagged
// file_engine::timing_tag (file_client::a_timing), answered here before the engine: nothing reaches storage.
namespace {
struct ATiming {
    U32 reads, read_units, read_max, writes, write_units, write_max;
    U32 others, other_units, gaps, gap_units, gap_max, unused;
};
static_assert(sizeof(ATiming) == 4 * file_engine::timing_words, "A's timing words");
ATiming a_timing __attribute__((section(".data"))) = {};
uint64_t a_last_end __attribute__((section(".data"))) = 0;
const uint64_t gap_bound = 20u * 400000u;                    // 20 ms
U32 units(uint64_t cycles) { return U32(cycles >> 10); }
U32 longest(U32 was, uint64_t cycles) { return cycles > was ? (cycles >> 32 ? 0xffffffffu : U32(cycles)) : was; }
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
        a_last_end = 0;
        session.reply(request, 0, 0);
    } else if (2 * k + 1 < file_engine::timing_words) session.reply(request, w[2 * k], w[2 * k + 1]);
    else session.reply(request, 0xffffffffu, error_access);
    return true;
}
// The serve hook: serve() timed.
void timed_serve(void* context, U32 request, Stock121PeerSession& session) {
    const uint64_t start = xyf::clock::cycles();
    if (timing_query(request, session)) return;
    const U32 vtable = word(request), op = byte(request + 5);
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
    } else { ++t.others; t.other_units += units(took); }
}
}
#if defined(XYF_MTP) || defined(XYF_USB_FILE_WRITE)
namespace xy_forever { namespace storage_server {
uint32_t engine_identity() {
    uint32_t bits = 0;
    for (U32 i = 0; i != xyf::mtp_spans::count; ++i) {
        const uint32_t* s = xyf::mtp_spans::table[i];
        if (crc(s[0], s[1]) != s[2]) bits |= 1u << (i & 31u);
    }
    return bits;
}
} }
#endif
