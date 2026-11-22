// target/mtp_usb_gate.hpp: which check refuses an MTP USB probe start. 186-003: the start uses the boot-time
// identity() result and still scans engine_identity()'s and probe_spans()'s spans: census words changed -> start;
// an MTP or the probe span changed -> refused, naming the check and span; boot-time check failed -> refused.
// Model memory: one byte per device address, derived from the address; the stock image is that memory
// with no word changed, so each table's CRCs are taken from it, at the real tables' addresses and lengths.
// A change sets one 32-bit word to a different value, as the device census saw (2026-11-15).
#include "doctest.h"
#include "mtp_usb_gate.hpp"
#include "mtp_spans.hpp"
#include "mtp_usb_spans.hpp"
#include "usb_spans.hpp"
#include "xyf_crc32.hpp"

namespace g = xyf::mtp_usb_gate;
namespace {
const uint32_t max_changes = 8;
uint32_t changed[max_changes];                 // word addresses that differ from the image now
uint32_t n_changed = 0;
uint8_t byte_at(uint32_t a, bool image) {
    uint8_t b = uint8_t((a * 2654435761u) >> 24);
    if (!image)
        for (uint32_t i = 0; i != n_changed; ++i)
            if ((a & ~3u) == changed[i]) b ^= 0xa5u;
    return b;
}
uint32_t crc_of(uint32_t address, uint32_t length, bool image) {
    uint32_t c = 0;
    for (uint32_t i = 0; i != length; ++i) {
        const uint8_t b = byte_at(address + i, image);
        c = xyf::crc::update(c, &b, 1);
    }
    return c;
}
uint32_t crc_now(uint32_t address, uint32_t length) { return crc_of(address, length, false); }
// A real table's addresses and lengths, with the model image's CRCs.
struct Table {
    uint32_t rows[128][3];
    g::Spans spans;
    Table(const uint32_t (*real)[3], uint32_t count) {
        REQUIRE_MESSAGE(count <= 128, "table fits");
        for (uint32_t i = 0; i != count; ++i) {
            rows[i][0] = real[i][0];
            rows[i][1] = real[i][1];
            rows[i][2] = crc_of(real[i][0], real[i][1], true);
        }
        spans.table = rows;
        spans.count = count;
    }
};
void set_changes(const uint32_t* words, uint32_t n) {
    n_changed = n;
    for (uint32_t i = 0; i != n; ++i) changed[i] = words[i];
}
const uint32_t census[] = {0x88a134acu, 0x88a134b0u, 0x88a134b8u, 0x88a137a0u, 0x88a321a4u};   // usb_spans #65, #86
}

TEST_CASE("the start gate against the span model") {
    const Table usb(xyf::usb_spans::table, xyf::usb_spans::count);
    const Table engine(xyf::mtp_spans::table, xyf::mtp_spans::count);
    const Table probe(xyf::mtp_usb_spans::table, xyf::mtp_usb_spans::count);
    uint32_t record[g::record_words];
    uint32_t scratch[g::record_words] = {0};

    // Nothing changed: start.
    set_changes(0, 0);
    REQUIRE_MESSAGE(g::refusal<crc_now>(true, engine.spans, probe.spans, record) == g::check_none, "clean: start");
    for (uint32_t i = 0; i != g::record_words; ++i) REQUIRE_MESSAGE(record[i] == 0, "clean: record zero");

    // (a) The live serial service changed the census words: identity() would refuse (#65 first, 2 spans),
    // the MTP checks still pass, and the boot-time check passed: start.
    set_changes(census, 5);
    REQUIRE_MESSAGE(g::first_bad<crc_now>(g::check_usb, usb.spans, scratch), "census: usb spans differ");
    REQUIRE_MESSAGE((scratch[g::r_index] == 65 && scratch[g::r_address] == 0x88a13460u &&
                     scratch[g::r_length] == 0x344u && scratch[g::r_failing] == 2),
                    "census: usb #65 first, two spans");
    for (uint32_t i = 0; i != g::record_words; ++i) record[i] = 0xdeadbeefu;
    REQUIRE_MESSAGE(g::refusal<crc_now>(true, engine.spans, probe.spans, record) == g::check_none, "census: start");
    for (uint32_t i = 0; i != g::record_words; ++i) REQUIRE_MESSAGE(record[i] == 0, "census: record cleared");

    // (b) An MTP span differs (with the census words too): refused, naming engine_identity() and the span.
    const uint32_t e = 40, engine_word = (xyf::mtp_spans::table[e][0] + 4u) & ~3u;
    const uint32_t with_engine[] = {census[0], census[1], census[2], census[3], census[4], engine_word};
    set_changes(with_engine, 6);
    REQUIRE_MESSAGE(g::refusal<crc_now>(true, engine.spans, probe.spans, record) == g::check_engine, "engine: refused");
    REQUIRE_MESSAGE(record[g::r_check] == g::check_engine, "engine: check named");
    uint32_t expect = 0;                                   // the first engine span holding a changed word
    while (expect != xyf::mtp_spans::count &&
           crc_now(engine.rows[expect][0], engine.rows[expect][1]) == engine.rows[expect][2])
        ++expect;
    REQUIRE_MESSAGE((expect <= e && record[g::r_index] == expect), "engine: first differing span");
    REQUIRE_MESSAGE((record[g::r_address] == xyf::mtp_spans::table[expect][0] &&
                     record[g::r_length] == xyf::mtp_spans::table[expect][1]),
                    "engine: span address and length");
    REQUIRE_MESSAGE((record[g::r_crc] == crc_now(engine.rows[expect][0], engine.rows[expect][1]) &&
                     record[g::r_crc] != engine.rows[expect][2]),
                    "engine: CRC as read");
    REQUIRE_MESSAGE(record[g::r_failing] >= 1, "engine: count");

    // (b) The probe span differs: refused, naming probe_spans() and span 0.
    const uint32_t probe_word = xyf::mtp_usb_spans::table[0][0] + 0x10u;
    const uint32_t with_probe[] = {census[0], census[1], census[2], census[3], census[4], probe_word};
    set_changes(with_probe, 6);
    REQUIRE_MESSAGE(!g::first_bad<crc_now>(g::check_engine, engine.spans, scratch), "probe: outside every engine span");
    REQUIRE_MESSAGE((g::refusal<crc_now>(true, engine.spans, probe.spans, record) == g::check_probe &&
                     record[g::r_check] == g::check_probe),
                    "probe: refused, check named");
    REQUIRE_MESSAGE((record[g::r_index] == 0 && record[g::r_address] == 0x88a0c0b0u && record[g::r_length] == 0x80u &&
                     record[g::r_crc] != probe.rows[0][2] && record[g::r_failing] == 1),
                    "probe: span named");

    // (c) The boot-time identity() had failed: refused before any scan, even with nothing changed now.
    set_changes(0, 0);
    REQUIRE_MESSAGE(g::refusal<crc_now>(false, engine.spans, probe.spans, record) == g::check_usb,
                    "boot failed: refused");
    REQUIRE_MESSAGE((record[g::r_check] == g::check_usb && record[g::r_index] == 0 && record[g::r_failing] == 0),
                    "boot failed: usb check named");
    set_changes(census, 5);
    REQUIRE_MESSAGE(g::refusal<crc_now>(false, engine.spans, probe.spans, record) == g::check_usb,
                    "boot failed, census: refused");
}
