#ifndef XYF_MOD_INTERNAL_HPP
#define XYF_MOD_INTERNAL_HPP
#include "mod_link.hpp"
// Internal parts of the mod linker (mod_link.cpp, mod_elf.cpp, mod_numbers.cpp).
namespace xyf_core { namespace mod_link {
typedef unsigned short U16;
// Host bridge only (-DXYF_MOD_WORK_COUNT): work units (bytes scanned or compared, headers decoded)
// summed here, so the tests can bound a step's work without timing it.
#ifdef XYF_MOD_WORK_COUNT
extern unsigned long long xyf_mod_work;
#define XYF_WORK(n) (xyf_mod_work += (n))
#else
#define XYF_WORK(n) ((void)0)
#endif

// profile.identities, the numbering rules (base identities.txt).
static const U32 max_rule_number = 1024, max_rule_list = 64;
struct Rules {
    U32 engines, engine_none, pages, page_none, effect_page;
    U32 stock_engines[max_rule_list], stock_engine_count;
    U32 sample_pages[max_rule_list], sample_page_count;
    U32 effects[max_rule_list], effect_count;
    U32 players[max_rule_list], player_count;
};

// Error text, truncated at the buffer's end (always NUL-terminated).
struct Text {
    char* buffer;
    U32 capacity, length;
    void add(const char* text);
    void add(const Byte* text, U32 count);
    void add_number(int value);
    void add_unsigned(U32 value);
};

namespace elf {
inline U32 u16(const Byte* p) { return U32(p[0]) | (U32(p[1]) << 8); }
inline U32 u32(const Byte* p) { return u16(p) | (u16(p + 2) << 16); }
enum Group { none = 0, text = 1, rodata = 2, data = 3, bss = 4 };
// max_name: the longest name (section, symbol, table) accepted; core.mjs has no limit.
static const U32 shn_abs = 0xfff1, max_bytes = 1u << 20, max_sections = 4096, max_name = 255;
struct Header {
    U32 start, count, names, symtab;
};
struct Section {
    U32 name, type, flags, offset, size, link, info, alignment, stride;
};
struct Symbol {
    U32 name, value, size, binding, type, section;
};
struct Relocation {
    U32 offset, type, symbol;
    int addend;
};
// Bytes check() needs beside the object (4-byte aligned): per section a list link, a list end and
// a first entry (the relocation tables of each target), plus one bit per byte of the object.
inline U32 work_bytes(U32 object_bytes) { return 8u * max_sections + (object_bytes >> 3) + 8; }
// readObject's checks, in its order, on the whole object. On failure `name`/`name_length` hold
// the section name core.mjs names (section_kind). Afterwards the accessors below are safe.
Status check(const Byte* data, U32 size, Byte* work, U32 work_size, Header& header, const Byte*& name,
             U32& name_length);
void section(const Byte* data, const Header& header, U32 index, Section& out);
void symbol(const Byte* data, const Header& header, U32 index, Symbol& out);
U32 symbol_count(const Byte* data, const Header& header);
// Name of a symbol (checked object): bytes after a UTF-8 BOM, as TextDecoder gives them.
void symbol_name(const Byte* data, const Header& header, const Symbol& s, const Byte*& text, U32& length);
U32 group(const Byte* data, const Header& header, U32 index);
// Relocation tables (type 4) in section order; entries of one table.
U32 entries(const Section& table);
void relocation(const Byte* data, const Section& table, U32 index, Relocation& out);
}

namespace numbers {
// numberLayout over fixed numbers, an optional layout and the machines to number; `out` holds the
// layout after (fixed, layout, then new machines by id). The work area is work_bytes(capacity).
struct Machine {
    const char* id;
    U32 kind;
    Identity requested;
};
enum Kind { synth = 0, drum = 1, sample = 2, effect = 3, player = 4, other = 5 };
U32 work_bytes(U32 capacity);
bool valid_rules(const Rules& rules);
bool valid_request(const Rules& rules, U32 kind, const Identity& identity);
bool fits(const Identity& requested, const Identity& numbers);
U32 slot_of(U32 kind);
Status layout(const Rules& rules, const Number* fixed, U32 fixed_count, const Number* layout, U32 layout_count,
              const Machine* machines, U32 machine_count, Byte* work, Number* out, U32 capacity, U32& count,
              Text& message);
U32 fnv1a(const Byte* text, U32 length);
}
}}
#endif
