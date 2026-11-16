#include "retained_lookup.hpp"
#include "cold_heap.hpp"
namespace xy_forever { namespace retained_lookup {
namespace {
bool span(const Range& range, U32 address, U32 bytes) {
    return range.begin <= address && address <= range.end && bytes <= range.end - address;
}
bool permitted(const Memory& m, U32 address, U32 bytes) {
    for (U32 i = 0; i != m.readable_count; ++i)
        if (span(m.readable[i], address, bytes)) return true;
    return false;
}
bool read(const Memory& m, U32 address, Byte* output, U32 count, Diagnostic& d) {
    d.offending_address = address;
    if (!permitted(m, address, count)) return false;
    ++d.reads;
    return m.read(m.user, address, output, count);
}
bool word(const Memory& m, U32 address, U32& output, Diagnostic& d) {
    Byte bytes[4];
    if ((address & 3u) || !read(m, address, bytes, 4, d)) return false;
    output = U32(bytes[0]) | (U32(bytes[1]) << 8) | (U32(bytes[2]) << 16) | (U32(bytes[3]) << 24);
    return true;
}
bool normalized(char* name, U32& begin, U32& length) {
    U32 end = 0;
    for (; end != name_capacity; ++end) {
        const Byte c = Byte(name[end]);
        if (!c) break;
        if (c < 32 || c >= 127) return false;
    }
    if (end == name_capacity) return false;
    begin = 0;
    while (begin != end && name[begin] == '/') ++begin;
    while (begin != end && name[end - 1] == '/') --end;
    length = end - begin;
    return true;
}
}
Result find_exact(const Memory& m, U32 sentinel, const char* name, U32& device, Diagnostic& d) {
    device = 0; d.visited = d.name_bytes = d.reads = d.offending_address = 0;
    if (!m.read || !m.readable || !m.readable_count || m.readable_count > 16 ||
        m.objects.begin > m.objects.end || !sentinel || (sentinel & 3u) ||
        sentinel > 0xfffffff7u || !name) return Result::argument;
    for (U32 i = 0; i != m.readable_count; ++i)
        if (m.readable[i].begin >= m.readable[i].end) return Result::argument;
    char query[name_capacity];
    U32 i = 0;
    for (; i != name_capacity; ++i) { query[i] = name[i]; if (!query[i]) break; }
    U32 query_begin = 0, query_length = 0;
    if (i == name_capacity || !normalized(query, query_begin, query_length) || !query_length)
        return Result::argument;
    U32 first = 0, tail = 0;
    if (!word(m, sentinel, first, d) || !word(m, sentinel + 4, tail, d)) return Result::read;
    U32 visited[max_nodes], previous = sentinel, current = first, candidate = 0;
    while (current != sentinel) {
        if (d.visited == max_nodes) return Result::capacity;
        for (U32 j = 0; j != d.visited; ++j)
            if (visited[j] == current) return Result::cycle;
        if ((current & 3u) || current < node_offset ||
            !span(m.objects, current - node_offset, device_bytes)) {
            d.offending_address = current; return Result::pointer;
        }
        visited[d.visited++] = current;
        const U32 object = current - node_offset;
        U32 next = 0, back = 0, path = 0;
        if (!word(m, current, next, d) || !word(m, current + 4, back, d) ||
            !word(m, object, path, d)) return Result::read;
        if (back != previous) return Result::links;
        char stored[name_capacity];
        for (i = 0; i != name_capacity; ++i) {
            Byte c = 0;
            if (path > 0xffffffffu - i || !read(m, path + i, &c, 1, d)) return Result::read;
            ++d.name_bytes; stored[i] = char(c);
            if (!c) break;
        }
        U32 begin = 0, length = 0;
        if (i == name_capacity || !normalized(stored, begin, length)) return Result::name;
        bool match = length == query_length;
        for (U32 j = 0; match && j != length; ++j)
            if (stored[begin + j] != query[query_begin + j]) match = false;
        if (match) {
            if (candidate) return Result::duplicate;
            candidate = object;
        }
        previous = current; current = next;
    }
    if (tail != previous) return Result::links;
    if (!candidate) return Result::missing;
    device = candidate; d.offending_address = 0; return Result::ok;
}
#if defined(__bfin__)
namespace {
bool native_read(void*, U32 address, Byte* output, U32 count) {
    const volatile Byte* input = reinterpret_cast<const volatile Byte*>(address);
    for (U32 i = 0; i != count; ++i) output[i] = input[i];
    return true;
}
}
Result stock121_yaffs_device(U32& device) {
    Diagnostic diagnostic;
    return stock121_yaffs_device(device, diagnostic);
}
Result stock121_yaffs_device(U32& device, Diagnostic& diagnostic) {
    device = 0;
    diagnostic.visited = diagnostic.name_bytes = diagnostic.reads = diagnostic.offending_address = 0;
    Result (*const self)(U32&, Diagnostic&) = &stock121_yaffs_device;
    const U32 code = reinterpret_cast<U32>(self);
    if (code >= cold_heap::arena_context && code < cold_heap::arena_end) return Result::placement;
    cold_heap::Snapshot heap;
    cold_heap::Result result;
    if (!cold_heap::guard(heap, result)) return Result::heap;
    // Union of final loaded ranges of the exact retained bootloader. No MMIO
    // or unmapped gap is accepted for list/name reads. Runtime allocations are
    // bounded separately by the checked current break, never by arena capacity.
    // L1 instruction SRAM (0x11a00000) is excluded: core data loads there are
    // not used. Tests 002/005/006, which loaded from it, all stalled.
    Range ranges[6] = {{0x0803c000u, 0x0803e4e0u}, {0x11800000u, 0x11804000u},
        {0x11900000u, 0x11908000u},
        {0x11b00000u, 0x11b01f00u}, {0x8803d000u, 0x88a63bf8u}, {heap.base, heap.brk}};
    const Memory memory = {0, native_read, ranges, heap.brk > heap.base ? 6u : 5u,
                           {heap.base, heap.brk}};
    return find_exact(memory, 0x11907f8cu, "/yaffs2", device, diagnostic);
}
#else
Result stock121_yaffs_device(U32& device) { device = 0; return Result::unavailable; }
Result stock121_yaffs_device(U32& device, Diagnostic& diagnostic) {
    device = 0;
    diagnostic.visited = diagnostic.name_bytes = diagnostic.reads = diagnostic.offending_address = 0;
    return Result::unavailable;
}
#endif
} }
