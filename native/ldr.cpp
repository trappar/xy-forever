#include "ldr.hpp"
namespace xyf_core {
namespace {
struct Record {
    U32 offset, flags, target, count, argument;
};
U32 word(const Byte* p) { return U32(p[0]) | U32(p[1]) << 8 | U32(p[2]) << 16 | U32(p[3]) << 24; }
Error read(const Byte* data, U32 size, U32 offset, Record& r) {
    if (offset > size || size - offset < 16) return Error::truncated;
    Byte sum = 0;
    for (U32 i = 0; i < 16; ++i) sum ^= data[offset + i];
    const U32 code = word(data + offset);
    if (sum || code >> 24 != 0xad) return Error::header;
    r = {offset, code & 65535, word(data + offset + 4), word(data + offset + 8), word(data + offset + 12)};
    if (r.count > 0xffffffffu - r.target) return Error::overflow;
    const U32 stored = r.flags == 0x104 ? 0 : r.count;
    if (stored > size - offset - 16) return Error::truncated;
    return Error::ok;
}
template<bool cooperative> Error memory(const Record& r, const Policy& p, Work* work) {
    if (!r.count) return Error::ok;
    bool allowed = false;
    for (U32 i = 0; i < p.allowed_count; ++i) {
        if (cooperative && (i & 31u) == 31u && !checkpoint(work)) return Error::cancelled;
        const Range& a = p.allowed[i];
        if (a.begin <= r.target && r.target + r.count <= a.end) allowed = true;
    }
    if (!allowed) return Error::range;
    for (U32 i = 0; i < p.protected_count; ++i) {
        if (cooperative && (i & 31u) == 31u && !checkpoint(work)) return Error::cancelled;
        const Range& a = p.protected_ranges[i];
        if (a.begin < r.target + r.count && r.target < a.end) return Error::protected_range;
    }
    return Error::ok;
}
template<bool cooperative>
Error inspect_image(const Byte* data, U32 size, const Policy& p, Summary& result, Work* work) {
    result = {0, 0, 0};
    if (!data || size < 32 || size > 16u * 1024u * 1024u) return Error::size;
    if ((p.allowed_count && !p.allowed) || (p.protected_count && !p.protected_ranges)) return Error::range;
    for (U32 i = 0; i < p.allowed_count; ++i) {
        if (cooperative && (i & 31u) == 31u && !checkpoint(work)) return Error::cancelled;
        if (p.allowed[i].begin >= p.allowed[i].end) return Error::range;
    }
    for (U32 i = 0; i < p.protected_count; ++i) {
        if (cooperative && (i & 31u) == 31u && !checkpoint(work)) return Error::cancelled;
        if (p.protected_ranges[i].begin >= p.protected_ranges[i].end) return Error::range;
    }
    U32 offset = 0, count = 0, expanded = 0;
    while (offset < size) {
        if (cooperative && !checkpoint(work)) return Error::cancelled;
        if (++count > 10000) return Error::capacity;
        Record r;
        Error e = read(data, size, offset, r);
        if (e != Error::ok) return e;
        if (count == 1) {
            if (r.flags != 0x5004 || r.count || r.argument != size - 16) return Error::first;
        } else if (r.flags == 0x8004) {
            if (r.count || r.argument || offset + 16 != size || r.target & 1) return Error::final;
            bool covered[2] = {false, false};
            U32 scan = 16;
            while (scan < offset) {
                if (cooperative && !checkpoint(work)) return Error::cancelled;
                Record previous;
                e = read(data, size, scan, previous);
                if (e != Error::ok) return e;
                for (U32 byte = 0; byte < 2; ++byte)
                    if (previous.target <= r.target + byte && r.target + byte - previous.target < previous.count)
                        covered[byte] = previous.flags == 4;
                scan += 16 + (previous.flags == 0x104 ? 0 : previous.count);
            }
            if (!covered[0] || !covered[1]) return Error::entry;
            result = {count, expanded, r.target};
            return Error::ok;
        } else {
            if (r.flags != 4 && r.flags != 0x104) return Error::flags;
            if (r.count > 256u * 1024u * 1024u - expanded) return Error::expanded;
            expanded += r.count;
            e = memory<cooperative>(r, p, work);
            if (e != Error::ok) return e;
        }
        offset += 16 + (r.flags == 0x104 ? 0 : r.count);
    }
    return Error::final;
}
}
Error inspect(const Byte* data, U32 size, const Policy& p, Summary& result) {
    // The cold boot path has no cooperative callback.
    return inspect_image<false>(data, size, p, result, nullptr);
}
Error inspect(const Byte* data, U32 size, const Policy& p, Summary& result, Work* work) {
    return work ? inspect_image<true>(data, size, p, result, work) : inspect(data, size, p, result);
}
Error expand(const Byte* data, U32 size, U32 offset, U32 delta, Byte* output, U32 capacity, U32& produced) {
    produced = 0;
    if (!data || !output || !capacity) return Error::capacity;
    Record r;
    Error e = read(data, size, offset, r);
    if (e != Error::ok) return e;
    if (r.flags != 4 && r.flags != 0x104) return Error::flags;
    if (delta > r.count) return Error::range;
    const U32 amount = capacity < r.count - delta ? capacity : r.count - delta;
    for (U32 i = 0; i < amount; ++i)
        output[i] = r.flags == 0x104 ? Byte(r.argument >> (((delta + i) & 3) * 8)) : data[offset + 16 + delta + i];
    produced = amount;
    return Error::ok;
}
const char* error_name(Error e) {
    switch (e) {
    case Error::ok: return "ok";
    case Error::size: return "size";
    case Error::truncated: return "truncated";
    case Error::header: return "header";
    case Error::flags: return "flags";
    case Error::first: return "first";
    case Error::overflow: return "overflow";
    case Error::expanded: return "expanded";
    case Error::final: return "final";
    case Error::entry: return "entry";
    case Error::range: return "range";
    case Error::protected_range: return "protected_range";
    case Error::capacity: return "capacity";
    case Error::cancelled: return "cancelled";
    }
    return "unknown";
}
}
