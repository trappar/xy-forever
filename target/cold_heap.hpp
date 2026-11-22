#ifndef XYF_COLD_HEAP_HPP
#define XYF_COLD_HEAP_HPP
#include <stdint.h>
namespace xy_forever { namespace cold_heap {
typedef uint32_t U32;
static const U32 staging_floor = 0x8a000000u;
static const U32 arena_context = 0x88a65bf8u;
static const U32 arena_base = 0x88a65c18u;
static const U32 arena_extent = 0x01ffffdcu;
static const U32 arena_end = 0x8aa65bf4u;
static const U32 max_free_nodes = 8192;
struct Snapshot {
    U32 context, count, base, extent, brk, next_context, cursor, free_head;
    U32 free_nodes, highest_free_end;
};
enum class Result { ok, unavailable, read, layout, staging_overlap, free_list, cursor, capacity };
struct Memory {
    void* user;
    bool (*word)(void*, U32 address, U32& value);
};
// Readonly pre-mount guard. The old allocator's break never decreases on free;
// brk<=staging_floor proves the initial outer LDR staging did not overwrite its
// allocated arena. Walks sorted free nodes, checks spans/heap IDs and cursor,
// and stops after8192 nodes. Never repairs, caps or initializes the allocator.
Result inspect(const Memory& memory, Snapshot& output);
// Exact stock1.2.1 metadata bindings. Run before any cold allocation, and keep
// the module/stack outside [arena_context,arena_end). No post-mount floor check:
// subsequent allocations may use the consumed outer-stream memory legitimately.
bool guard(Snapshot& output, Result& result);
}}
#endif
