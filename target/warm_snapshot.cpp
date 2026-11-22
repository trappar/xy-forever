#include "warm_snapshot.hpp"
#include "../native/trace_slot.hpp"
#include "cold_storage.hpp"
#include "cold_heap.hpp"
#include "census.hpp"

// The cold report travels inside the loader stream: it is built on the loader
// stack, then replaces one admitted DATA record of the copied image, and the
// ROM loader writes it to its destination. Nothing here writes, reads or
// flushes the OS carry address directly (the direct carry path failed).
extern "C" bool xy_forever_patch_warm_snapshot(xyf_core::Byte* image, xyf_core::U32 image_bytes,
                                               const xyf_core::Policy& policy) {
    using namespace xy_forever;
    using namespace storage_probe;
    Report report;
    // Test017 (diagnostic version 11): per-processor cold memory census into a
    // zero DATA slot below the reporter; report summarizes the slot result.
    {   // keep this scope: without it the stack frame, and so the candidate, changes
        volatile Byte* clean = reinterpret_cast<volatile Byte*>(&report);
        for (U32 i = 0; i != sizeof(report); ++i) clean[i] = 0;
        report.safe_handoff = 1;
        report.diagnostic.magic = 0x53444358u;
        report.diagnostic.version = 11;
        report.diagnostic.result = cold_storage::Result::unavailable;
        report.begin_result = U32(cold_storage::Result::unavailable);
        cold_heap::guard(report.heap, report.heap_result);
        cold_storage::capture_state(report.diagnostic.before);
        const U32 gpio = *reinterpret_cast<volatile U32*>(0x2004000cu);
        const xyf_core::trace_slot::Result slot =
            xyf_core::trace_slot::locate_zero(image, image_bytes, policy, XYF_CENSUS_BASE, XYF_CENSUS_BYTES);
        report.read_result = U32(slot.status);
        if (slot.status == xyf_core::trace_slot::Status::ok) {
            report.marker_size =
                census::fill(image + slot.file_offset, XYF_CENSUS_BYTES, report.diagnostic.before.role, gpio);
            report.begin_result = U32(report.marker_size ? cold_storage::Result::ok : cold_storage::Result::device);
        }
        report.marker_read = gpio;
        seal(report);
    }
    const xyf_core::trace_slot::Result result =
        xyf_core::trace_slot::replace(image, image_bytes, policy, carry_base, xy_forever_trace_expected,
                                      reinterpret_cast<const Byte*>(&report), sizeof(report));
    if (result.status != xyf_core::trace_slot::Status::ok) return false;
    // Use the same CRC-checked cache-aware copy adapter as the ROM reader,
    // on the already-used stream buffer, rather than on an untouched OS page.
    typedef void Copy(void*, const void*, U32);
    reinterpret_cast<Copy*>(0x88a346a6u)(image + result.file_offset, &report, sizeof(report));
    for (U32 i = 0; i != sizeof(report); ++i)
        if (image[result.file_offset + i] != reinterpret_cast<const Byte*>(&report)[i]) return false;
    return true;
}
