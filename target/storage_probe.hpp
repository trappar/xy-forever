#ifndef XYF_STORAGE_PROBE_HPP
#define XYF_STORAGE_PROBE_HPP
#include "cold_storage.hpp"
#include "cold_heap.hpp"
namespace xy_forever { namespace storage_probe {
using services::Byte;
using services::U32;
static const U32 reporter_base = 0x8d4ce380u;
static const U32 carry_base = 0x8d4cf380u;
static const U32 reservation_end = 0x8d4d0380u;
static const U32 marker_bytes = 512;
// Direct-carry experiments exclude this range from all inner OS writes/fills.
// Fixture/stream experiments intentionally load a checked DATA record here.
// Cold data and its writer run on GPIO role0; no shared-processor DDR is assumed.
struct Report {
    U32 magic, version, bytes, checksum;
    U32 begin_result, read_result, cleanup_result, safe_handoff;
    U32 marker_size, marker_read, marker_mismatches;
    cold_storage::Diagnostic diagnostic;
    cold_heap::Result heap_result;
    cold_heap::Snapshot heap;
};
static_assert(sizeof(Report) <= 4096, "Carry record exceeds its reservation");
static_assert(sizeof(Report) == 360, "Report decoder ABI changed");
Byte marker_byte(U32 index);
void seal(Report& report);
bool valid(const Report& report);
// Test012 pre-copy probe result, carried by the stream patch.
extern Report fresh_report;
} }
#endif
