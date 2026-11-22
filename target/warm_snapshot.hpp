#ifndef XYF_WARM_SNAPSHOT_HPP
#define XYF_WARM_SNAPSHOT_HPP
#include "storage_probe.hpp"
#include "../native/ldr.hpp"
extern "C" const xyf_core::Byte
    xy_forever_trace_expected[sizeof(xy_forever::storage_probe::Report)] __asm__("xy_forever_trace_expected");
extern "C" bool xy_forever_patch_warm_snapshot(xyf_core::Byte*, xyf_core::U32, const xyf_core::Policy&);
#endif
