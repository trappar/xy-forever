#ifndef XYF_FILE_BOOT_HPP
#define XYF_FILE_BOOT_HPP
#include <stdint.h>
#include "cold_storage.hpp"
#include "peer_services.hpp"
#include "xyf_boot.hpp"
// File boot (Test025; the menu boot built on it is described in xyf_boot.hpp):
// boot one firmware FILE on BOTH processors, every boot.
//  - a (role 0, storage): read-only fresh mount, read the chosen image into the
//    staging buffer, admit it (cold LDR policy, entry, report slot, census
//    slot), unmount, then serve it to b over the stock SPI link (frozen
//    SHA-256, peer_boot Server). No file or refusal: decline b's select.
//  - b (role 1, screen/keys): wait for a, receive the image into staging,
//    verify CRC + SHA-256 + the same admission, then restore and release.
//  - Agreement: each processor boots the file only if it COMMITTED (role 0:
//    release 7 arrived after a completed transfer and restore; role 1: the
//    release reply after restore was zero). Otherwise both boot the embedded
//    payload (test scaffolding). a's last refusal point is restore, because
//    the stock slave ISR acknowledges release 7 by itself.
//  - After commit the staged image is copied to the RAM-boot source
//    0x8a000000; cold.cpp re-admits it (CRC + LDR policy) before RAM boot.
// Staging sits outside the retained heap arena reservation [0x88a65bf8,
// 0x8aa65bf4) that the peer session refuses for DMA/frozen buffers.
namespace xy_forever { namespace file_boot {
static const uint32_t magic = 0x31544246u;          // "FBT1"
static const uint32_t boot_buffer = 0x8a000000u;
static const uint32_t scratch = 0x8b800000u, scratch_bytes = 0x10000u;
static const uint32_t staging = 0x8b820000u, staging_bytes = 0x7e0000u;   // to 0x8c000000
static const uint32_t role0_idle_ms = 3000;    // a: no request from b for 3 s -> give up
enum Outcome : uint32_t {
    outcome_none = 0,
    outcome_file = 1,
    outcome_no_image = 2,
    outcome_refused = 3,
    outcome_link = 4,
    outcome_copy = 5,
    outcome_role = 6
};
// Load/admission reasons (0 = admitted).
enum Reason : uint32_t {
    reason_ok = 0,
    reason_storage = 1,
    reason_open = 2,
    reason_size = 3,
    reason_read = 4,
    reason_policy = 5,
    reason_entry = 6,
    reason_report_slot = 7,
    reason_census_slot = 8,
    reason_heap = 9,
    reason_unmount = 10,
    reason_not_run = 11,
    reason_no_library = 12,
    reason_mismatch = 13,     // library boot: nothing bootable / info.txt size or CRC
    reason_content = 14,
    reason_content_repair = 15   // its content is not installed / a switch is unfinished
};
// Library boot: a picks the image from the XY Forever library (xyf_cold_library).
struct LibraryInfo {
    uint32_t error, choice, count, skipped;            // choice: 0 menu, 1 once, 2 default
    uint32_t index, expected_bytes, expected_crc, flags; // flags: 1 has_bytes, 2 has_crc, 4 matched
    char id[32];
};
static_assert(sizeof(LibraryInfo) == 64, "LibraryInfo layout");
struct Result {
    uint32_t magic, version, role, outcome;
    uint32_t storage_begin, storage_end, storage_safe, load_reason;
    uint32_t image_bytes, image_crc, admit_reason, read_result;
    uint32_t begin_result, poll_result, end_result, polls;
    uint32_t committed, declined, copy_crc, heap_brk;
    uint32_t start_cycles, storage_ms, hash_ms,
        link_ms;   // b: storage_ms = a's first command answer, hash_ms = the longest wait for one
    uint32_t admit_ms, end_ms, stack_used, received_bytes;
    uint32_t digest[8];
    cold_storage::Diagnostic storage;
    peer_services::Diagnostic diagnostic;
    LibraryInfo library;                               // since version 2
    xyf_boot::MenuInfo menu;                           // since version 3 (after library)
#if defined(XYF_MENU_ON_FAILURE)
    xyf_boot::TimingInfo timing;                       // version 5 (product), after menu
    xyf_boot::TimingDetail detail;                     // version 6 (product), after timing
#endif
#if defined(XYF_STAGE2)
    xyf_boot::Stage2Info stage2;                       // version 4 only (after menu)
#endif
};
static const uint32_t library_bytes = sizeof(LibraryInfo);
static const uint32_t menu_bytes = sizeof(xyf_boot::MenuInfo);
#if defined(XYF_MENU_ON_FAILURE)
static const uint32_t timing_bytes = sizeof(xyf_boot::TimingInfo) + sizeof(xyf_boot::TimingDetail);
#else
static const uint32_t timing_bytes = 0;
#endif
#if defined(XYF_STAGE2)
static const uint32_t stage2_bytes = sizeof(xyf_boot::Stage2Info);
#else
static const uint32_t stage2_bytes = 0;
#endif
static_assert(sizeof(Result) == 36 * 4 + sizeof(cold_storage::Diagnostic) + sizeof(peer_services::Diagnostic) +
                                    library_bytes + menu_bytes + timing_bytes + stage2_bytes,
              "Result layout");
extern Result result;
#if defined(XYF_TFW_CHECK)
void discard_prepared(); // A: revoke normal CHECK/BOOT lease before developer staging
#endif
}}
// Pre-copy hook (both roles). Sets the storage-image handoff variables only
// after a commit; always returns.
extern "C" void xy_forever_file_boot();
#endif
