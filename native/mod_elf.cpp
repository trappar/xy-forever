// ELF32 relocatable subset: a port of open-xy builder/core.mjs readObject (MIT, commit 52c8de9).
// Same checks in the same order, so a malformed object gets the refusal core.mjs gives.
#include "mod_internal.hpp"

namespace xyf_core { namespace mod_link { namespace elf {
namespace {
const U32 sht_symtab = 2, sht_rela = 4, sht_nobits = 8, sht_rel = 9;

bool fits_in(U32 offset, U32 count, U32 size) { return count <= size && offset <= size - count; }

// TextDecoder('utf-8', {fatal: true}).decode: strict UTF-8; a leading BOM is dropped.
bool utf8(const Byte* p, U32 n) {
    U32 i = 0;
    while (i < n) {
        U32 c = p[i];
        if (c < 0x80) {
            ++i;
            continue;
        }
        U32 need, low = 0x80, high = 0xbf;
        if (c >= 0xc2 && c <= 0xdf) need = 1;
        else if (c >= 0xe0 && c <= 0xef) {
            need = 2;
            if (c == 0xe0) low = 0xa0;
            if (c == 0xed) high = 0x9f;
        } else if (c >= 0xf0 && c <= 0xf4) {
            need = 3;
            if (c == 0xf0) low = 0x90;
            if (c == 0xf4) high = 0x8f;
        } else return false;
        if (need > n - i - 1) return false;
        for (U32 k = 1; k <= need; ++k) {
            U32 b = p[i + k];
            if (b < (k == 1 ? low : 0x80) || b > (k == 1 ? high : 0xbf)) return false;
        }
        i += need + 1;
    }
    return true;
}

// core.mjs string(table, at) over a section's data (empty for NOBITS).
Status string(const Byte* data, const Section& table, U32 at, const Byte*& text, U32& length) {
    U32 size = table.type == sht_nobits ? 0 : table.size;
    if (at >= size) return Status::string_offset;
    const Byte* base = data + table.offset;
    U32 end = at;
    while (end < size && base[end] && end - at <= max_name) ++end;
    XYF_WORK(2 * (end - at) + 1);
    if (end - at > max_name) return Status::name_length;
    if (end == size) return Status::string_end;
    if (!utf8(base + at, end - at)) return Status::utf8;
    text = base + at;
    length = end - at;
    if (length >= 3 && text[0] == 0xef && text[1] == 0xbb && text[2] == 0xbf) {
        text += 3;
        length -= 3;
    }
    return Status::ok;
}

// /^word(?:\.|$)/
bool prefix(const Byte* text, U32 length, const char* word) {
    U32 count = 0;
    while (word[count]) ++count;
    if (length < count) return false;
    for (U32 i = 0; i != count; ++i)
        if (text[i] != Byte(word[i])) return false;
    return length == count || text[count] == '.';
}
U32 group_of(const Byte* n, U32 length) {
    if (prefix(n, length, ".rodata") || prefix(n, length, ".data.rel.ro")) return rodata;
    if (prefix(n, length, ".data") || prefix(n, length, ".sdata")) return data;
    if (prefix(n, length, ".bss") || prefix(n, length, ".sbss")) return bss;
    if (prefix(n, length, ".text")) return text;
    return none;
}
bool relative(U32 type) { return type == 10 || type == 13; }
bool supported(U32 type) { return type == 6 || type == 7 || type == 10 || type == 13 || type == 18; }
}

void section(const Byte* data, const Header& header, U32 index, Section& s) {
    const Byte* p = data + header.start + index * 40;
    XYF_WORK(1);
    s.name = u32(p);
    s.type = u32(p + 4);
    s.flags = u32(p + 8);
    s.offset = u32(p + 16);
    s.size = u32(p + 20);
    s.link = u32(p + 24);
    s.info = u32(p + 28);
    s.alignment = u32(p + 32) ? u32(p + 32) : 1;
    s.stride = u32(p + 36);
}
U32 symbol_count(const Byte* data, const Header& header) {
    Section table;
    section(data, header, header.symtab, table);
    return table.size >> 4;
}
void symbol(const Byte* data, const Header& header, U32 index, Symbol& s) {
    Section table;
    section(data, header, header.symtab, table);
    const Byte* p = data + table.offset + index * 16;
    s.name = u32(p);
    s.value = u32(p + 4);
    s.size = u32(p + 8);
    s.binding = p[12] >> 4;
    s.type = p[12] & 15;
    s.section = u16(p + 14);
}
void symbol_name(const Byte* data, const Header& header, const Symbol& s, const Byte*& text, U32& length) {
    Section table, strings;
    section(data, header, header.symtab, table);
    section(data, header, table.link, strings);
    text = 0;
    length = 0;
    string(data, strings, s.name, text, length);
}
U32 group(const Byte* data, const Header& header, U32 index) {
    Section s, names;
    section(data, header, index, s);
    if (!(s.flags & 2)) return none;
    section(data, header, header.names, names);
    const Byte* text = 0;
    U32 length = 0;
    if (string(data, names, s.name, text, length) != Status::ok) return none;
    return group_of(text, length);
}
U32 entries(const Section& table) { return table.size / 12u; }
void relocation(const Byte* data, const Section& table, U32 index, Relocation& r) {
    const Byte* p = data + table.offset + index * 12;
    XYF_WORK(1);
    U32 info = u32(p + 4);
    r.offset = u32(p);
    r.type = info & 255;
    r.symbol = info >> 8;
    r.addend = int(u32(p + 8));
}

Status check(const Byte* d, U32 size, Byte* work, U32 work_size, Header& h, const Byte*& name, U32& name_length) {
    name = 0;
    name_length = 0;
    if (!d || size < 52 || size > max_bytes) return Status::object_size;
    if (d[0] != 0x7f || d[1] != 'E' || d[2] != 'L' || d[3] != 'F' || d[4] != 1 || d[5] != 1 || u16(d + 16) != 1 ||
        u16(d + 18) != 106)
        return Status::object_type;
    h.start = u32(d + 32);
    h.count = u16(d + 48);
    h.names = u16(d + 50);
    h.symtab = 0;
    if (u16(d + 46) != 40 || !h.count || h.count >= max_sections || h.names >= h.count ||
        !fits_in(h.start, h.count * 40, size))
        return Status::section_table;
    Section s, t;
    for (U32 i = 0; i != h.count; ++i) {
        section(d, h, i, s);
        if (s.size > max_bytes || s.alignment > 4096 || (s.alignment & (s.alignment - 1))) return Status::section_size;
        if (s.type != sht_nobits && !fits_in(s.offset, s.size, size)) return Status::section_file;
    }
    Section names;
    section(d, h, h.names, names);
    for (U32 i = 0; i != h.count; ++i) {
        section(d, h, i, s);
        const Byte* text = 0;
        U32 length = 0;
        Status status = string(d, names, s.name, text, length);
        if (status != Status::ok) return status;
        if (!(s.flags & 2)) continue;
        if (s.flags & ~7u) return Status::section_flags;
        U32 g = group_of(text, length);
        if (g == none || s.type != (g == bss ? sht_nobits : 1u)) {
            name = text;
            name_length = length;
            return Status::section_kind;
        }
    }
    U32 tables = 0;
    for (U32 i = 0; i != h.count; ++i) {
        section(d, h, i, s);
        if (s.type == sht_symtab) {
            ++tables;
            h.symtab = i;
        }
    }
    if (tables != 1) return Status::symbol_tables;
    Section table;
    section(d, h, h.symtab, table);
    if (table.stride != 16 || (table.size & 15) || table.link >= h.count) return Status::symbol_table;
    Section strings;
    section(d, h, table.link, strings);
    U32 symbols = table.size >> 4;
    for (U32 i = 0; i != symbols; ++i) {
        const Byte* p = d + table.offset + i * 16;
        U32 shndx = u16(p + 14);
        if (shndx >= h.count && shndx != shn_abs) return Status::symbol_section;
        const Byte* text = 0;
        U32 length = 0;
        Status status = string(d, strings, u32(p), text, length);
        if (status != Status::ok) return status;
    }
    // Relocations: every check but the overlap one, in order, up to the first failure; then the
    // overlap check over the entries before that point (one bitmap per target section, in turn).
    // The first overlap before the failure is core.mjs's refusal; otherwise the failure is.
    U32 passed = 0;
    Status failure = Status::ok;
    for (U32 i = 0; i != h.count && failure == Status::ok; ++i) {
        section(d, h, i, s);
        if (s.type == sht_rel) {
            failure = Status::rel;
            break;
        }
        if (s.type != sht_rela) continue;
        if (s.link != h.symtab || s.info >= h.count || s.stride != 12 || s.size % 12u) {
            failure = Status::relocation_table;
            break;
        }
        section(d, h, s.info, t);
        if (group(d, h, s.info) == none || t.type == sht_nobits) {
            failure = Status::relocation_target;
            break;
        }
        U32 count = entries(s);
        for (U32 k = 0; k != count; ++k) {
            Relocation r;
            relocation(d, s, k, r);
            if (!supported(r.type) || r.symbol >= symbols) {
                failure = Status::relocation_type;
                break;
            }
            U32 width = r.type == 18 ? 4 : 2;
            if ((r.offset & 1) || !fits_in(r.offset, width, t.size) || (relative(r.type) && r.offset < 2)) {
                failure = Status::relocation_range;
                break;
            }
            ++passed;
        }
    }
    if (!passed) return failure;
    if (!work || work_size < work_bytes(size)) return Status::argument;
    // Each target section's tables are a list in section order (next[], built through last[]), so
    // every table and entry is visited a bounded number of times; a target's bits are cleared after
    // it (the bitmap is all clear between targets).
    const U32 end = 0xffff, done = 0xfffe;
    U16* next = reinterpret_cast<U16*>(work);
    U16* last = next + max_sections;
    U32* start = reinterpret_cast<U32*>(last + max_sections);
    Byte* bits = reinterpret_cast<Byte*>(start + max_sections);
    for (U32 i = 0; i != h.count; ++i) last[i] = U16(end);
    for (U32 i = 0; i <= (size >> 3); ++i) bits[i] = 0;
    XYF_WORK(h.count + (size >> 3));
    U32 seen = 0, listed = 0;
    for (U32 i = 0; i != h.count && seen < passed; ++i) {
        section(d, h, i, s);
        if (s.type != sht_rela) continue;
        start[i] = seen;
        next[i] = U16(end);
        if (last[s.info] != end) next[last[s.info]] = U16(i);
        last[s.info] = U16(i);
        U32 count = entries(s);
        seen += count < passed - seen ? count : passed - seen;
        listed = i + 1;
    }
    U32 first = passed;                                // first overlapping entry, in order
    for (U32 i = 0; i != listed; ++i) {
        section(d, h, i, s);
        if (s.type != sht_rela || last[s.info] == done) continue;
        last[s.info] = U16(done);
        for (U32 clear = 0; clear != 2; ++clear)       // set and test the bits, then clear them
            for (U32 j = i; j != end; j = next[j]) {
                section(d, h, j, t);
                U32 count = entries(t);
                if (count > passed - start[j]) count = passed - start[j];
                for (U32 k = 0; k != count && (clear || start[j] + k < first); ++k) {
                    Relocation r;
                    relocation(d, t, k, r);
                    U32 from = relative(r.type) ? r.offset - 2 : r.offset, to = r.offset + (r.type == 18 ? 4 : 2);
                    bool overlap = false;
                    for (U32 b = from; b != to; ++b) {
                        Byte bit = Byte(1u << (b & 7));
                        if (bits[b >> 3] & bit) overlap = true;
                        bits[b >> 3] = clear ? Byte(0) : Byte(bits[b >> 3] | bit);
                    }
                    if (overlap && !clear) {
                        first = start[j] + k;
                        break;
                    }
                }
            }
    }
    return first < passed ? Status::relocation_overlap : failure;
}
}}}
