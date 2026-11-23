#ifndef XYF_INFLATE_HPP
#define XYF_INFLATE_HPP
#include "ldr.hpp"
#include "work.hpp"

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
// Keep the indirect callback load beside the call in the cold module.
static __attribute__((noinline, unused)) bool write_to_sink(const Sink& sink, const Byte* bytes, U32 count) {
    return sink.write(sink.context, bytes, count);
}

enum class Status {
    ok,
    argument,
    io,
    truncated,
    unsupported,
    layout,
    name,
    duplicate,
    bounds,
    capacity,
    deflate,
    checksum,
    sink,
    cancelled
};
struct Extraction {
    U32 produced, crc32, consumed;
};
// table (#279): every code in one or two look-ups. The next root input bits (deflate's bit order) index the root
// table. An entry is symbol << 4 | length, or for a longer code table_link | start << 3 | bits: a second table at
// start, indexed by the bits input bits after those. 0: no code there (an unused code, or no room left for its
// second table), decoded bit by bit from the canonical tables, as is a code the input ends inside.
static const U32 table_entries = 1536, table_link = 0x8000u;
struct Huffman {
    U32 count[16], first[16], offset[16], root;
    unsigned short symbols[288], table[table_entries];
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
Status inflate_raw(const Reader& reader, U32 offset, U32 compressed_size, U32 expected_size,
                   InflateWorkspace& workspace, const Sink& sink, Extraction& result, Work* work = nullptr);
const char* status_name(Status status);
}}
#endif
