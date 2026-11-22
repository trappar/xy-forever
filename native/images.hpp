#ifndef XYF_IMAGES_HPP
#define XYF_IMAGES_HPP
#include "companions.hpp"

namespace xyf_core { namespace images {
enum class Status { ok, argument, size, encoding, header, layout, checksum, vectors };
struct CyacdSummary {
    U32 silicon_id, revision, checksum_type, rows;
};
struct BleSummary {
    U32 stack, reset, crc16;
};
// Only the OP-XY application layout observed in both supplied releases:
// silicon100a11a0 revision0 checksum0, array0 rows25..390 then511,256bytes each.
Status inspect_cyacd(const Byte* data, U32 size, CyacdSummary& result);
// Copy one validated application row into caller-owned 256-byte storage.
// This validates the whole image first and never emits a partial row.
// Output and result storage must not overlap immutable input bytes.
Status cyacd_row(const Byte* data, U32 size, U32 index, Byte* output, U32 capacity, companion::CypressRow& result);
// Vector/bounds checks match the existing Python application inspector. They
// distinguish the separately shipped Nordic bootloader; no chip is contacted.
Status inspect_ble(const Byte* data, U32 size, BleSummary& result);
}}
#endif
