#ifndef XYF_MTP_USB_GATE_HPP
#define XYF_MTP_USB_GATE_HPP
// Which check refuses an MTP USB probe start (test build xyf-mtp-usb only: usb_service.cpp
// probe_start; host test target/mtp_usb_gate_test.cpp). The record says why (op 1 sends it after the
// probe words): the check, its first differing span (index, address, length), that span's CRC as read
// and how many of its spans differ. The span, not the word: B holds only CRCs
// (scripts/mtp_span_census.py finds the word). All zero: no refusal.
#include <stdint.h>
namespace xyf { namespace mtp_usb_gate {
// usb: the boot-time identity() (usb_spans.hpp, menu_start_usb) had failed; engine: engine_identity()
// (mtp_spans.hpp); probe: probe_spans() (mtp_usb_spans.hpp).
enum Check : uint32_t { check_none, check_usb, check_engine, check_probe };
const uint32_t record_words = 6;
enum : uint32_t { r_check, r_index, r_address, r_length, r_crc, r_failing };
// crc_at(address, length): CRC-32 of stock code at a device address; a template argument, so the device
// calls it directly (the builder admits no indirect call).
typedef uint32_t CrcAt(uint32_t address, uint32_t length);
struct Spans { const uint32_t (*table)[3]; uint32_t count; };     // {address, length, CRC of the stock image}

template<CrcAt crc_at> bool first_bad(uint32_t check, const Spans& s, uint32_t* record) {
    uint32_t failing = 0;
    for (uint32_t i = 0; i != s.count; ++i) {
        const uint32_t c = crc_at(s.table[i][0], s.table[i][1]);
        if (c == s.table[i][2]) continue;
        if (!failing++) {
            record[r_index] = i; record[r_address] = s.table[i][0];
            record[r_length] = s.table[i][1]; record[r_crc] = c;
        }
    }
    if (failing) { record[r_check] = check; record[r_failing] = failing; }
    return failing != 0;
}
// identity() (usb_spans.hpp) is not run again here: the serial USB service, live since the menu started
// it, changes five stock words in its spans #65 (0x88a13460) and #86 (0x88a32128) (device census
// 2026-11-15). The result of the boot-time run is used instead (rule: the MTP start skips
// that second fingerprint; the power-on one stays, and the two MTP-specific ones stay and pass).
// The MTP checks still run, in order, before anything is switched; a failed one names its check and
// span.
template<CrcAt crc_at> Check refusal(bool boot_identity_passed, const Spans& engine, const Spans& probe,
                                     uint32_t* record) {
    for (uint32_t i = 0; i != record_words; ++i) record[i] = 0;
    if (!boot_identity_passed) { record[r_check] = check_usb; return check_usb; }
    if (first_bad<crc_at>(check_engine, engine, record)) return check_engine;
    if (first_bad<crc_at>(check_probe, probe, record)) return check_probe;
    return check_none;
}
} }
#endif
