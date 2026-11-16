#ifndef XYF_INFLATE_HPP
#define XYF_INFLATE_HPP
#include "ldr.hpp"

namespace xyf_core { namespace zip {
// Readers must fill the requested byte range exactly, or return false. Sinks
// receive provisional bytes: a later CRC/format error invalidates all output.
struct Reader {
    void* context;
    U32 size;
    bool (*read)(void*, U32 offset, Byte* output, U32 count);
};
struct Sink {
    void* context;
    bool (*write)(void*, const Byte*, U32 count); // null discards validated output
};
enum class Status {
    ok, argument, io, truncated, unsupported, layout, name, duplicate,
    bounds, capacity, deflate, checksum, sink
};
struct Extraction { U32 produced, crc32, consumed; };
struct Huffman {
    U32 count[16], first[16], offset[16], symbols[288];
};
// Caller owns this workspace. It must not overlap the Entry table, source
// reader storage or sink storage and cannot be shared by concurrent calls.
struct InflateWorkspace {
    Byte window[32768];
    Byte input[512];
    Byte lengths[320];
    Huffman literal, distance, code;
};
U32 crc32_update(U32 running, const Byte* data, U32 count);
Status inflate_raw(const Reader& reader, U32 offset, U32 compressed_size,
                   U32 expected_size, InflateWorkspace& workspace,
                   const Sink& sink, Extraction& result);
const char* status_name(Status status);
} }
#endif
