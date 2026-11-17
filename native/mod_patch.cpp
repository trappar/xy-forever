// Mod assembly, patch step: a port of open-xy builder/core.mjs patchFirmware.
#include "mod_patch.hpp"
namespace xyf_core { namespace mod_patch {
namespace {
enum Kind { heap = 1, table, text, hook, startup };
enum Flag { call = 1, profiling_off = 2, has_after = 4 };
enum Phase { check_stock, wait_module, resolve, apply, check_image, inspect_image, done, failed };
const Byte no_service = 0xff;
const unsigned short no_symbol = 0xffff;
__attribute__((noinline)) U32 le16(const Byte* p) { return U32(p[0]) | U32(p[1]) << 8; }
U32 le32(const Byte* p) { return le16(p) | le16(p + 2) << 16; }
__attribute__((noinline)) void put16(Byte* p, U32 v) { p[0] = Byte(v); p[1] = Byte(v >> 8); }
void put32(Byte* p, U32 v) { put16(p, v); put16(p + 2, v >> 16); }
U32 round4(U32 n) { return (n + 3u) & ~3u; }
// Official open-xy bases (docs/STORAGE.md, "Mod packages"), laid out as Profile's first 96 bytes (stock and
// catalog SHA-256, then stock_bytes, stock_records, module_base, reserved_bytes, module_bytes,
// heap_fill_base, heap_fill_bytes, expanded_base), then edits.bin's SHA-256. The first 92 bytes and the edits
// make a base known; its expanded_base must match too only when the larger layout is used, so a base
// written before that key (expanded_base 0) stays known for the standard layout.
struct KnownBase { Byte stock[32], catalog[32]; U32 numbers[8]; Byte edits[32]; };
const U32 known_bytes = 92;
typedef char profile_facts_are_92_bytes[__builtin_offsetof(Profile, expanded_base) == known_bytes ? 1 : -1];
const KnownBase known_bases[] = {
#include "mod_known_bases.inc"
};
bool same_bytes(const Byte* a, const Byte* b, U32 n) {
    for (U32 i = 0; i != n; ++i) if (a[i] != b[i]) return false;
    return true;
}
bool known_base(const Profile& f, const hash::Digest& edits) {
    for (U32 k = 0; k != sizeof(known_bases) / sizeof(known_bases[0]); ++k)
        if (same_bytes(reinterpret_cast<const Byte*>(&known_bases[k]), reinterpret_cast<const Byte*>(&f), known_bytes) &&
            same_bytes(known_bases[k].edits, edits.bytes, 32) &&
            (!f.expanded || known_bases[k].numbers[7] == f.expanded_base)) return true;
    return false;
}
// core.mjs header(): a loader record with its XOR checksum in byte 2.
void header(Byte* out, U32 flags, U32 target, U32 count, U32 argument) {
    put32(out, 0xad000000u | flags); put32(out + 4, target); put32(out + 8, count); put32(out + 12, argument);
    Byte sum = 0;
    for (U32 i = 0; i != 16; ++i) sum ^= out[i];
    out[2] = sum;
}
// Half-open ranges of nonzero length; no 64-bit arithmetic.
bool overlaps(U32 a, U32 a_count, U32 b, U32 b_count) {
    if (!a_count || !b_count) return false;
    return a < b ? b - a < a_count : a - b < b_count;
}
bool inside(U32 a, U32 a_count, U32 b, U32 b_count) {   // [a, a+a_count) within [b, b+b_count)
    return a >= b && a - b <= b_count && a_count <= b_count - (a - b);
}
// The refusal and its detail, "<what> <number>" (digits by subtraction: no division helper).
Status refuse(Patcher& p, Status status, const char* what, U32 number = 0xffffffffu) {
    static const U32 powers[10] = {1000000000u, 100000000u, 10000000u, 1000000u, 100000u, 10000u, 1000u, 100u, 10u, 1u};
    U32 n = 0;
    while (what[n] && n < detail_bytes - 12) { p.detail[n] = what[n]; ++n; }
    if (number != 0xffffffffu) {
        p.detail[n++] = ' ';
        bool started = false;
        for (U32 k = 0; k != 10; ++k) {
            char d = '0';
            while (number >= powers[k]) { number -= powers[k]; ++d; }
            if (d != '0' || started || k == 9) { p.detail[n++] = d; started = true; }
        }
    }
    p.detail[n] = 0;
    p.phase = failed;
    p.failure = status;
    return status;
}

Status parse_edits(Patcher& p) {
    const Byte* data = p.edits;
    const U32 size = p.edits_bytes;
    p.edit_count = p.service_count = 0;
    if (size < 12 || size > max_edits_file || data[0] != 'X' || data[1] != 'Y' || data[2] != 'F' || data[3] != 'E' ||
        le16(data + 4) != 1) return refuse(p, Status::edits, "header");
    const U32 count = le16(data + 6), services = le16(data + 8), string_bytes = le16(data + 10);
    if (count > p.limit_edits || services > max_services || 12 + 2 * services > size ||
        string_bytes > size - 12 - 2 * services) return refuse(p, Status::edits, "counts");
    p.strings = 12 + 2 * services; p.string_bytes = string_bytes;
    const Byte* strings = data + p.strings;
    if (string_bytes && strings[string_bytes - 1]) return refuse(p, Status::edits, "strings");
    for (U32 i = 0; i != services; ++i) {
        const U32 at = le16(data + 12 + 2 * i);
        if (at >= string_bytes) return refuse(p, Status::edits, "service", i);
        p.service_names[i] = (unsigned short)at;
    }
    p.service_count = services;
    U32 at = p.strings + string_bytes;
    for (U32 i = 0; i != count; ++i) {
        if (size - at < 16) return refuse(p, Status::edits, "edit", i);
        const Byte* r = data + at;
        Edit& e = p.table[i];
        e.kind = r[0]; e.flags = r[1]; e.service = r[2]; e.active = 0;
        e.address = le32(r + 4); e.offset = le32(r + 8);
        e.length = (unsigned short)le16(r + 12); e.symbol = (unsigned short)le16(r + 14);
        const bool fixed = e.kind == heap || e.kind == table || e.kind == text;
        if (r[3] || e.kind < heap || e.kind > startup || (e.flags & ~7u) || !e.length || e.length > max_edit_bytes ||
            (e.service != no_service && e.service >= services) || (e.symbol != no_symbol && e.symbol >= string_bytes) ||
            fixed != bool(e.flags & has_after) || ((e.flags & (call | profiling_off)) && e.kind != hook) ||
            (e.service != no_service && e.kind != hook && e.kind != text)) return refuse(p, Status::edits, "edit", i);
        at += 16;
        const U32 stored = (e.flags & has_after) ? 2u * e.length : e.length;
        if (size - at < stored) return refuse(p, Status::edits, "edit", i);
        e.before = at; e.after = (e.flags & has_after) ? at + e.length : 0;
        at += stored;
    }
    if (at != size) return refuse(p, Status::edits, "trailing");
    p.edit_count = count;
    return Status::ok;
}

// core.mjs readLdr on image[0, size): every record header.
Status parse_records(Patcher& p, U32 size, Status status) {
    p.record_count = 0;
    for (U32 at = 0; at < size;) {
        if (p.record_count == p.limit_records) return refuse(p, Status::capacity, "records");
        if (size - at < 16) return refuse(p, status, "record", p.record_count);
        const Byte* h = p.image + at;
        Byte sum = 0;
        for (U32 i = 0; i != 16; ++i) sum ^= h[i];
        const U32 code = le32(h), target = le32(h + 4), count = le32(h + 8);
        const U32 stored = (code & 256) ? 0 : count;
        if (sum || code >> 24 != 0xad || (target && count > 0u - target) || stored > size - at - 16)
            return refuse(p, status, "record", p.record_count);
        Record& r = p.records[p.record_count++];
        r.offset = at; r.target = target; r.count = count; r.code = code;
        at += 16 + stored;
        if (code & 0x8000) return at == size ? Status::ok : refuse(p, status, "after final");
    }
    return refuse(p, status, "no final");
}

void sift(unsigned short* v, U32 root, U32 n, const Edit* e) {
    for (U32 guard = 0; guard != 32; ++guard) {
        U32 child = 2 * root + 1;
        if (child >= n) return;
        if (child + 1 < n && e[v[child]].offset < e[v[child + 1]].offset) ++child;
        if (!(e[v[root]].offset < e[v[child]].offset)) return;
        const unsigned short t = v[root]; v[root] = v[child]; v[child] = t;
        root = child;
    }
}
// Sort by file offset; false when two edits overlap (core.mjs "Overlapping patch intervals").
bool sort_edits(Patcher& p, U32& at) {
    const U32 n = p.edit_count;
    for (U32 i = 0; i != n; ++i) p.order[i] = (unsigned short)i;
    for (U32 i = n / 2; i;) sift(p.order, --i, n, p.table);
    for (U32 end = n; end > 1;) {
        --end;
        const unsigned short t = p.order[0]; p.order[0] = p.order[end]; p.order[end] = t;
        sift(p.order, 0, end, p.table);
    }
    for (U32 i = 1; i < n; ++i) {
        const Edit& a = p.table[p.order[i - 1]];
        if (p.table[p.order[i]].offset - a.offset < a.length) { at = p.order[i]; return false; }
    }
    return true;
}

// core.mjs redirect(): the replacement bytes of a hook or the startup label.
bool redirect(Edit& e, U32 destination) {
    for (U32 i = 0; i != 12; ++i) e.redirect[i] = 0;
    if (destination & 1) return false;
    if (e.kind == startup) {
        if (e.length != 10) return false;
        put16(e.redirect, 0xdc00); put16(e.redirect + 2, destination >> 16); put16(e.redirect + 4, destination & 0xffff);
        return true;
    }
    const bool is_call = e.flags & call;
    if (e.length == 8 || e.length == 12) {
        if (!is_call && e.length != 8) return false;
        put16(e.redirect, is_call ? 0xde00 : 0xdc00);
        put16(e.redirect + 2, destination >> 16); put16(e.redirect + 4, destination & 0xffff);
        return true;
    }
    const U32 delta = destination - e.address;   // two's complement, even, within +-16 MiB
    if ((e.length != 4 && e.length != 6) || (delta & 1) || (delta >= 0x1000000u && delta < 0xff000000u)) return false;
    const U32 value = (delta >> 1) & 0xffffffu;
    put16(e.redirect, (is_call ? 0xe300u : 0xe200u) | value >> 16);
    put16(e.redirect + 2, value & 0xffff);
    return true;
}
const Byte* replacement(const Patcher& p, const Edit& e) {
    if (!e.active) return p.edits + e.before;
    return (e.kind == hook || e.kind == startup || (e.kind == heap && p.layout.expanded)) ? e.redirect
                                                                                           : p.edits + e.after;
}
// A Blackfin+ Dreg/Preg = imm32 load (core.mjs heapLoad): its opcode and value, or false.
bool heap_load(const Byte* b, U32 length, U32& opcode, U32& value) {
    opcode = le16(b);
    value = le16(b + 2) << 16 | le16(b + 4);
    return length == 8 && (opcode & 0xffc0u) == 0xda00u && !le16(b + 6);
}

bool profile_ok(const Profile& f) {
    return f.stock_bytes >= 32 && f.stock_bytes <= (16u << 20) - 16 && f.stock_records && f.module_bytes &&
           f.module_bytes <= f.reserved_bytes && f.reserved_bytes <= 0xffffffffu - f.module_base &&
           f.module_bytes <= (16u << 20) - 16 - f.stock_bytes;
}
const U32 profile_key_count = 10, required_keys = 9;
const char* const profile_keys[profile_key_count] = {"needs_os", "catalog_sha256", "stock_bytes", "stock_records",
    "module_base", "reserved_bytes", "module_bytes", "heap_fill_base", "heap_fill_bytes", "expanded_module_base"};
U32 hex_digit(Byte c) {
    c = Byte(c | 0x20u);
    return c >= '0' && c <= '9' ? c - '0' : c >= 'a' && c <= 'f' ? c - 'a' + 10u : 16u;
}
bool parse_number(const Byte* s, U32 n, U32& v) {    // decimal below 2^32, or 0x + 1-8 hex digits
    v = 0;
    if (n > 2 && s[0] == '0' && (s[1] | 0x20u) == 'x') {
        if (n > 10) return false;
        for (U32 i = 2; i != n; ++i) {
            const U32 d = hex_digit(s[i]);
            if (d == 16) return false;
            v = v << 4 | d;
        }
        return true;
    }
    if (!n || n > 10) return false;
    for (U32 i = 0; i != n; ++i) {
        const U32 d = U32(s[i]) - '0';
        if (d > 9 || v > 429496729u || (v == 429496729u && d > 5)) return false;
        v = v * 10u + d;
    }
    return true;
}
bool parse_digest(const Byte* s, U32 n, hash::Digest& d) {   // 64 lowercase hex digits
    if (n != 64) return false;
    for (U32 i = 0; i != 64; ++i) {
        const U32 x = hex_digit(s[i]);
        if (x == 16 || (s[i] >= 'A' && s[i] <= 'F')) return false;
        d.bytes[i >> 1] = Byte(i & 1 ? d.bytes[i >> 1] << 4 | x : x);
    }
    return true;
}
bool same_key(const Byte* s, U32 n, const char* key) {
    U32 i = 0;
    while (i != n && key[i] && s[i] == Byte(key[i])) ++i;
    return i == n && !key[i];
}
__attribute__((noinline)) Status profile_refusal(char (&detail)[detail_bytes], const char* what) {
    U32 n = 0;
    while (what[n] && n + 1 < detail_bytes) { detail[n] = what[n]; ++n; }
    detail[n] = 0;
    return Status::base;
}
Status setup(Patcher& p, const Profile& profile, const Byte* edits, U32 edits_bytes, Byte* image, U32 capacity,
             const Policy& policy, const Limits& limits, Byte* scratch, U32 scratch_capacity) {
    p.phase = failed; p.failure = Status::argument; p.detail[0] = 0;
    p.profile = &profile; p.policy = &policy; p.link = 0; p.linked = 0;
    p.edits = edits; p.edits_bytes = edits_bytes; p.image = image; p.capacity = capacity;
    p.edit_count = p.record_count = p.service_count = p.cursor = p.module_bytes = p.total = p.applied = 0;
    p.ldr.records = p.ldr.expanded_bytes = p.ldr.entry = 0;
    p.profiling = p.known = false;
    const U32 need = scratch_bytes(limits);
    if (!edits || !image || !need || !scratch || (reinterpret_cast<unsigned long>(scratch) & 3) ||
        scratch_capacity < need || !limits.slice_bytes || !profile_ok(profile))
        return refuse(p, Status::argument, "arguments");
    p.table = reinterpret_cast<Edit*>(scratch);
    p.records = reinterpret_cast<Record*>(scratch + limits.edits * sizeof(Edit));
    p.order = reinterpret_cast<unsigned short*>(scratch + limits.edits * sizeof(Edit) + limits.records * sizeof(Record));
    p.limit_edits = limits.edits; p.limit_records = limits.records; p.slice_bytes = limits.slice_bytes;
    p.whole.reset(); p.restored.reset();
    return Status::ok;
}
// The base before any byte of edits.bin is used: known (or confirmed unverified), edits.bin's hash, then
// its edits; then the layout in use (p.profile from here on). Expanded (core.mjs expandedProfile, checked as
// offersExpandedMemory checks the heap edits): exactly two heap edits, each loading heap_fill_bytes in stock
// and the standard heap (module_base - heap_fill_base) after; both then load expanded_base - heap_fill_base
// with the same instruction (kept in their redirect bytes).
Status admit(Patcher& p, const hash::Digest& edits_sha256, bool unverified_confirmed) {
    const Profile& f = *p.profile;
    p.known = known_base(f, edits_sha256);
    if (!p.known && !unverified_confirmed) return refuse(p, Status::unverified, "base");
    hash::Digest d;
    p.whole.update(p.edits, p.edits_bytes); p.whole.finish(d); p.whole.reset();
    if (!hash::equal(d, edits_sha256)) return refuse(p, Status::edits, "sha256");
    const Status s = parse_edits(p);
    if (s != Status::ok) return s;
    if (!layout(f, p.layout)) return refuse(p, Status::base, "expanded");
    p.profile = &p.layout;
    if (!f.expanded) return Status::ok;
    U32 heaps = 0;
    for (U32 i = 0; i != p.edit_count; ++i) {
        Edit& e = p.table[i];
        if (e.kind != heap) continue;
        U32 op_before, before, op_after, after;
        if (++heaps > 2 || !heap_load(p.edits + e.before, e.length, op_before, before) ||
            !heap_load(p.edits + e.after, e.length, op_after, after) || op_before != op_after ||
            before != f.heap_fill_bytes || after != f.module_base - f.heap_fill_base)
            return refuse(p, Status::base, "expanded edit", i);
        const U32 value = p.layout.module_base - f.heap_fill_base;
        put16(e.redirect, op_after); put16(e.redirect + 2, value >> 16); put16(e.redirect + 4, value);
        put16(e.redirect + 6, 0);
    }
    return heaps == 2 ? Status::ok : refuse(p, Status::base, "expanded edits");
}
// Each edit inside one stored (not zero-fill) record's payload of records[0, count), at its file offset.
Status check_mapping(Patcher& p, U32 count) {
    for (U32 i = 0; i != p.edit_count; ++i) {
        const Edit& e = p.table[i];
        U32 covering = 0, at = 0;
        for (U32 r = 0; r != count; ++r)
            if (overlaps(p.records[r].target, p.records[r].count, e.address, e.length)) { ++covering; at = r; }
        const Record& c = p.records[at];
        if (covering != 1 || (c.code & 256) || !inside(e.address, e.length, c.target, c.count) ||
            c.offset + 16 + (e.address - c.target) != e.offset) return refuse(p, Status::mapping, "edit", i);
    }
    return Status::ok;
}
// Hash image[from, to) into the rebuilt-stock hash with the edits undone (sorted order).
void hash_restored(Patcher& p, U32 from, U32 to) {
    U32 at = from;
    for (U32 i = 0; i != p.edit_count && at < to; ++i) {
        const Edit& e = p.table[p.order[i]];
        if (e.offset + e.length <= at) continue;
        if (e.offset >= to) break;
        if (e.offset > at) { p.restored.update(p.image + at, e.offset - at); at = e.offset; }
        const U32 end = e.offset + e.length < to ? e.offset + e.length : to;
        p.restored.update(p.edits + e.before + (at - e.offset), end - at);
        at = end;
    }
    if (at < to) p.restored.update(p.image + at, to - at);
}

Status check_stock_layout(Patcher& p) {
    const Profile& f = *p.profile;
    const Status s = parse_records(p, f.stock_bytes, Status::layout);
    if (s != Status::ok) return s;
    const Record& first = p.records[0];
    const Record& final = p.records[p.record_count - 1];
    if (p.record_count != f.stock_records || first.count || (first.code & 0xffff) != 0x5004 || final.count ||
        (final.code & 0xffff) != 0x8004) return refuse(p, Status::layout, "stock records");
    U32 overlapping = 0, fill = 0;
    for (U32 i = 0; i != p.record_count; ++i)
        if (overlaps(p.records[i].target, p.records[i].count, f.module_base, f.reserved_bytes)) { ++overlapping; fill = i; }
    const Record& z = p.records[fill];
    if (overlapping != 1 || !(z.code & 256) || le32(p.image + z.offset + 12) || z.target != f.heap_fill_base ||
        z.count != f.heap_fill_bytes || !inside(f.module_base, f.reserved_bytes, z.target, z.count))
        return refuse(p, Status::layout, "reservation");
    if (check_mapping(p, p.record_count) != Status::ok) return p.failure;
    for (U32 i = 0; i != p.edit_count; ++i) {
        const Edit& e = p.table[i];
        for (U32 b = 0; b != e.length; ++b)
            if (p.image[e.offset + b] != p.edits[e.before + b]) return refuse(p, Status::preimage, "edit", i);
    }
    U32 at = 0;
    if (!sort_edits(p, at)) return refuse(p, Status::overlap, "edit", at);
    return Status::ok;
}

// Whether services.txt row `name` (an edits.bin string) is one of the link's active services.
bool service_active(const Patcher& p, U32 name) {
    const char* text; U32 length;
    for (U32 i = 0; i != 32 && mod_link::service_at(*p.link, i, text, length); ++i) {
        if (!((p.linked->services >> i) & 1u) || name + length >= p.string_bytes) continue;
        bool same = !p.edits[p.strings + name + length];
        for (U32 k = 0; k != length && same; ++k) same = p.edits[p.strings + name + k] == Byte(text[k]);
        if (same) return true;
    }
    return false;
}

Status resolve_edits(Patcher& p) {
    const Profile& f = *p.profile;
    const Byte* strings = p.edits + p.strings;
    for (U32 i = 0; i != p.edit_count; ++i) {
        Edit& e = p.table[i];
        bool active = !((e.flags & profiling_off) && p.profiling);
        if (e.service != no_service) active = active && service_active(p, p.service_names[e.service]);
        e.active = active;
        if (active && (e.kind == hook || e.kind == startup)) {
            // linked.globals.get(symbol): edits.bin's strings are NUL-terminated (parse_edits)
            U32 destination = 0;
            const bool found = e.symbol != no_symbol &&
                mod_link::global(*p.link, reinterpret_cast<const char*>(strings + e.symbol), destination);
            if (!found || destination < f.module_base || destination - f.module_base >= p.module_bytes)
                return refuse(p, Status::destination, "edit", i);
            if (!redirect(e, destination)) return refuse(p, Status::branch, "edit", i);
        }
        const Byte* after = replacement(p, e);
        for (U32 b = 0; b != e.length; ++b) if (after[b] != p.edits[e.before + b]) { ++p.applied; break; }
    }
    return Status::ok;
}

void apply_edits(Patcher& p) {
    const Profile& f = *p.profile;
    const U32 final_at = f.stock_bytes - 16;
    for (U32 i = 0; i != 16; ++i) p.final[i] = p.image[final_at + i];
    for (U32 i = 0; i != p.edit_count; ++i) {
        const Edit& e = p.table[i];
        const Byte* after = replacement(p, e);
        for (U32 b = 0; b != e.length; ++b) p.image[e.offset + b] = after[b];
    }
    for (U32 i = 0; i != 16; ++i) p.image[f.stock_bytes + p.module_bytes + i] = p.final[i];
    header(p.image + final_at, 4, f.module_base, p.module_bytes, 0);
    header(p.image, 0x5004, le32(p.image + 4), 0, p.total - 16);
}

// One slice: the image as assembled, and stock rebuilt from it (edits undone, record removed).
Status check_slice(Patcher& p) {
    const Profile& f = *p.profile;
    const U32 record = f.stock_bytes - 16, module_end = f.stock_bytes + p.module_bytes;
    const U32 from = p.cursor, to = p.total - from < p.slice_bytes ? p.total : from + p.slice_bytes;
    p.whole.update(p.image + from, to - from);
    if (from == 0) {
        Byte first[16];
        header(first, 0x5004, le32(p.image + 4), 0, f.stock_bytes - 16);
        p.restored.update(first, 16);
    }
    const U32 a = from < 16 ? 16 : from, b = to < record ? to : record;
    if (a < b) hash_restored(p, a, b);
    const U32 c = from < module_end ? module_end : from;
    if (c < to) p.restored.update(p.image + c, to - c);
    p.cursor = to;
    if (to < p.total) return Status::more;
    hash::Digest d;
    p.restored.finish(d);
    // A patch's only proof of the stock it was given (190: not hashed before as well): a difference is the stock's.
    if (!hash::equal(d, f.stock_sha256))
        return p.link ? refuse(p, Status::stock, "sha256") : refuse(p, Status::verify, "restored sha256");
    p.whole.finish(p.sha256);
    return Status::ok;
}
}

U32 scratch_bytes(const Limits& l) {
    if (!l.edits || l.edits > max_edits || l.records < 2 || l.records > max_records) return 0;
    return l.edits * U32(sizeof(Edit)) + l.records * U32(sizeof(Record)) + round4(2 * l.edits);
}

Status start(Patcher& p, const Profile& profile, const Byte* edits, U32 edits_bytes, const hash::Digest& edits_sha256,
             Byte* image, U32 capacity, bool profiling, bool unverified_confirmed, const Policy& policy,
             const Limits& limits, Byte* scratch, U32 scratch_capacity) {
    Status s = setup(p, profile, edits, edits_bytes, image, capacity, policy, limits, scratch, scratch_capacity);
    if (s != Status::ok || (s = admit(p, edits_sha256, unverified_confirmed)) != Status::ok) return s;
    if (capacity < profile.stock_bytes + 16) return refuse(p, Status::capacity, "image");
    p.profiling = profiling;
    p.phase = check_stock;
    return Status::more;
}

Status set_module(Patcher& p, const mod_link::Workspace& link, const mod_link::Result& linked) {
    if (p.phase != wait_module) return p.phase == failed ? p.failure : refuse(p, Status::argument, "not waiting");
    const Profile& f = *p.profile;
    if (linked.status != mod_link::Status::ok || linked.module_base != f.module_base || linked.limit != f.module_bytes)
        return refuse(p, Status::argument, "link result");
    const U32 module_bytes = linked.sizes.initialized;
    if (!module_bytes || module_bytes > linked.sizes.allocated || linked.sizes.allocated > f.module_bytes)
        return refuse(p, Status::module, "allocated", linked.sizes.allocated);
    if (module_bytes > p.capacity - f.stock_bytes - 16) return refuse(p, Status::capacity, "image");
    p.link = &link; p.linked = &linked;
    const char* name; U32 length;
    for (U32 i = 0; i != 32; ++i) {          // every active service is the profile's
        if (!((linked.services >> i) & 1u)) continue;
        if (!mod_link::service_at(link, i, name, length)) return refuse(p, Status::service, "service", i);
        bool known = false;
        for (U32 j = 0; j != p.service_count && !known; ++j) {
            const U32 at = p.service_names[j];
            known = at + length < p.string_bytes && !p.edits[p.strings + at + length];
            for (U32 k = 0; k != length && known; ++k) known = p.edits[p.strings + at + k] == Byte(name[k]);
        }
        if (!known) return refuse(p, Status::service, "service", i);
    }
    p.module_bytes = module_bytes;
    p.total = f.stock_bytes + 16 + module_bytes;
    p.phase = resolve;
    return Status::more;
}

Status step(Patcher& p) {
    Status s;
    switch (p.phase) {
    case check_stock:    // loader layout, reservation, every edit's mapping and preimage, overlaps
        if ((s = check_stock_layout(p)) != Status::ok) return s;
        p.phase = wait_module;
        return Status::wait;
    case wait_module: return Status::wait;
    case resolve:        // each edit's bytes: stock (inactive), redirected, or fixed
        if ((s = resolve_edits(p)) != Status::ok) return s;
        p.phase = apply;
        return Status::more;
    case apply:          // in place: edits, FINAL after the module, the module record, the first record
        apply_edits(p);
        p.cursor = 0;
        p.phase = check_image;
        return Status::more;
    case check_image:
        if ((s = check_slice(p)) != Status::ok) return s;
        p.phase = inspect_image;
        return Status::more;
    case inspect_image: {
        const Error e = inspect(p.image, p.total, *p.policy, p.ldr);
        if (e != Error::ok) return refuse(p, Status::policy, error_name(e));
        p.phase = done;
        return Status::ok;
    }
    case done: return Status::ok;
    case failed: return p.failure;
    }
    return Status::argument;
}

Status start_verify(Patcher& p, const Profile& profile, const Byte* edits, U32 edits_bytes,
                    const hash::Digest& edits_sha256, const Byte* image, U32 size, bool unverified_confirmed,
                    const Policy& policy, const Limits& limits, Byte* scratch, U32 scratch_capacity, bool hashed) {
    Status s = setup(p, profile, edits, edits_bytes, const_cast<Byte*>(image), size, policy, limits, scratch,
                     scratch_capacity);
    if (s != Status::ok || (s = admit(p, edits_sha256, unverified_confirmed)) != Status::ok) return s;
    const Profile& f = *p.profile;          // the layout in use (admit)
    const U32 stock = f.stock_bytes;
    if (size <= stock + 16 || size - stock - 16 > f.module_bytes) return refuse(p, Status::verify, "size");
    p.module_bytes = size - stock - 16;
    p.total = size;
    if (parse_records(p, size, Status::verify) != Status::ok) return p.failure;
    const Record& first = p.records[0];
    const Record& final = p.records[p.record_count - 1];
    Byte want[16];
    header(want, 4, f.module_base, p.module_bytes, 0);
    if (p.record_count != f.stock_records + 1 || p.records[p.record_count - 2].offset != stock - 16 ||
        !same_bytes(image + stock - 16, want, 16) || (first.code & 0xffff) != 0x5004 || first.count ||
        le32(image + 12) != size - 16 ||
        (final.code & 0xffff) != 0x8004 || final.count || final.offset != stock + p.module_bytes)
        return refuse(p, Status::verify, "records");
    // Stock's records are the image's before the module record (which sits where FINAL was).
    if (check_mapping(p, p.record_count - 2) != Status::ok) return p.failure;
    U32 at = 0;
    if (!sort_edits(p, at)) return refuse(p, Status::verify, "edit", at);
    p.phase = hashed ? inspect_image : check_image;
    return Status::more;
}

bool layout(const Profile& f, Profile& out) {
    for (U32 i = 0; i != 32; ++i) { out.stock_sha256.bytes[i] = f.stock_sha256.bytes[i]; out.catalog_sha256.bytes[i] = f.catalog_sha256.bytes[i]; }
    out.stock_bytes = f.stock_bytes; out.stock_records = f.stock_records; out.module_base = f.module_base;
    out.reserved_bytes = f.reserved_bytes; out.module_bytes = f.module_bytes; out.heap_fill_base = f.heap_fill_base;
    out.heap_fill_bytes = f.heap_fill_bytes; out.expanded_base = f.expanded_base; out.expanded = f.expanded;
    if (!f.expanded) return true;
    // core.mjs offersExpandedMemory's size rules: whole MiB, larger, the same end, above the heap's start
    const U32 x = f.expanded_base, end = f.module_base + f.reserved_bytes, mib = (1u << 20) - 1;
    // (profile_ok, true of every Profile read_profile gives, rules out overflow of module_base + reserved_bytes)
    if (!x || x >= f.module_base || x <= f.heap_fill_base || f.heap_fill_bytes != end - f.heap_fill_base ||
        (f.reserved_bytes & mib) || ((end - x) & mib) || ((x - f.heap_fill_base) & mib) ||
        ((f.module_base - f.heap_fill_base) & mib))
        return false;
    out.module_base = x;
    out.reserved_bytes = end - x;
    out.module_bytes = f.module_bytes + (f.module_base - x);   // the tables' bytes stay at the top
    return profile_ok(out);
}

Status read_profile(const Byte* info, U32 bytes, Profile& f, char (&detail)[detail_bytes]) {
    U32* numbers[profile_key_count - 2] = {&f.stock_bytes, &f.stock_records, &f.module_base, &f.reserved_bytes,
                                           &f.module_bytes, &f.heap_fill_base, &f.heap_fill_bytes, &f.expanded_base};
    U32 seen = 0;
    f.expanded_base = 0; f.expanded = false;
    detail[0] = 0;
    if (!info) bytes = 0;
    for (U32 at = 0; at < bytes;) {
        U32 end = at;
        while (end != bytes && info[end] != '\n') ++end;
        const Byte* line = info + at;
        const U32 n = end - at;
        at = end + 1;
        U32 eq = 0;
        while (eq != n && line[eq] != '=') ++eq;
        if (!n || line[0] == '#' || eq == n) continue;
        U32 k = 0;
        while (k != profile_key_count && !same_key(line, eq, profile_keys[k])) ++k;
        if (k == profile_key_count) continue;
        const Byte* value = line + eq + 1;
        const U32 vn = n - eq - 1;
        if (((seen >> k) & 1u) || !(k > 1 ? parse_number(value, vn, *numbers[k - 2]) :
                                     parse_digest(value, vn, k ? f.catalog_sha256 : f.stock_sha256)))
            return profile_refusal(detail, profile_keys[k]);
        seen |= 1u << k;
    }
    for (U32 k = 0; k != required_keys; ++k)
        if (!((seen >> k) & 1u)) return profile_refusal(detail, profile_keys[k]);
    return profile_ok(f) ? Status::ok : profile_refusal(detail, "profile");
}

const char* status_name(Status s) {
    switch (s) {
    case Status::ok: return "ok";
    case Status::more: return "more";
    case Status::wait: return "wait";
    case Status::argument: return "argument";
    case Status::capacity: return "capacity";
    case Status::edits: return "edits";
    case Status::stock: return "stock";
    case Status::layout: return "layout";
    case Status::mapping: return "mapping";
    case Status::preimage: return "preimage";
    case Status::service: return "service";
    case Status::destination: return "destination";
    case Status::branch: return "branch";
    case Status::overlap: return "overlap";
    case Status::module: return "module";
    case Status::verify: return "verify";
    case Status::policy: return "policy";
    case Status::base: return "base";
    case Status::unverified: return "unverified";
    }
    return "unknown";
}
} }
