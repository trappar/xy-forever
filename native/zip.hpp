#ifndef XYF_ZIP_HPP
#define XYF_ZIP_HPP
#include "inflate.hpp"

namespace xyf_core { namespace zip {
struct Limits {
    U32 entries, member_bytes, total_bytes, archive_bytes, expansion_ratio;
    Limits() : entries(4096), member_bytes(64u * 1024u * 1024u),
        total_bytes(256u * 1024u * 1024u), archive_bytes(256u * 1024u * 1024u),
        expansion_ratio(1024) {}
};
// ZIP32 names use a deliberately narrow portable ASCII subset (max255 bytes).
// Both known official packages fit it; Unicode/ZIP64/encryption are unsupported.
struct Entry {
    char name[256];
    U32 name_size, local_offset, data_offset, compressed_size, size, crc32;
    U32 method, flags, local_end;
    bool directory;
};
struct Archive {
    Reader reader;
    Limits limits;
    const Entry* entries;
    U32 count, total_bytes, directory_offset;
};
// No extraction or writes. The entry table is caller-owned, must remain
// immutable and must outlive Archive. Source bytes must also remain immutable.
Status open(const Reader& reader, const Limits& limits, Entry* entries,
            U32 capacity, Archive& archive);
// Extracts a single indexed member to a caller sink, or validates with null sink.
// On any failure all sink output is provisional and must be discarded.
Status extract(const Archive& archive, U32 index, InflateWorkspace& workspace,
               const Sink& sink, Extraction& result);
} }
#endif
