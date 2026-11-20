#include "xyf_mods.hpp"
#include "xyf_menu.hpp"
#include "xyf_store.hpp"
#include "ui_pages.hpp"
#include "usb_proto_ids.hpp"
#include "xyf_settings.hpp"
#include "../native/mod_job.hpp"
// The Mods page's pure part (xyf_mods.hpp). Every loop is bounded by its input or a fixed size.
namespace xyf { namespace mods {
namespace {
using namespace xyf::menu;
namespace mj = xyf_core::mod_job;
bool same(const char* a, const char* b) { while (*a && *a == *b) { ++a; ++b; } return *a == *b; }
// Unsigned decimal value (at most 9 digits: no overflow); false when it is not one.
bool number(const char* s, uint32_t& out) {
    uint32_t v = 0, n = 0;
    for (; s[n]; ++n) { if (s[n] < '0' || s[n] > '9' || n == 9) return false; v = v * 10u + uint32_t(s[n] - '0'); }
    out = v;
    return n != 0;
}
// "0x" and 1-8 hex digits; false when it is not one.
bool hex(const char* s, uint32_t& out) {
    if (s[0] != '0' || s[1] != 'x') return false;
    uint32_t v = 0, n = 2;
    for (; s[n]; ++n) {
        const char c = s[n];
        const uint32_t d = c >= '0' && c <= '9' ? uint32_t(c - '0') : c >= 'a' && c <= 'f' ? uint32_t(c - 'a' + 10) :
                           c >= 'A' && c <= 'F' ? uint32_t(c - 'A' + 10) : 16u;
        if (d == 16u || n == 10) return false;
        v = v << 4 | d;
    }
    out = v;
    return n > 2;
}
// Copies s (at most max chars) into the pool; the offset, or 0xffff when it does not fit.
uint16_t keep(Page& p, const char* s, uint32_t max) {
    uint32_t n = 0;
    while (s[n] && n < max) ++n;
    if (p.pool_used + n + 1 > pool_bytes) return 0xffffu;
    const uint32_t at = p.pool_used;
    for (uint32_t i = 0; i != n; ++i) p.pool[at + i] = s[i];
    p.pool[at + n] = 0;
    p.pool_used += n + 1;
    return uint16_t(at);
}
void put(char*& at, const char* end, const char* s) { while (*s && at < end) *at++ = *s++; }
void put_number(char*& at, const char* end, uint32_t v) { char d[12]; decimal(d, v); put(at, end, d); }
bool token_char(char c, bool dot) {
    return (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_' || c == '-' || (dot && c == '.');
}
bool token(const char* t, bool dot) {           // mods.apply's job_token: 1-31 of a-z 0-9 _ - (.), alnum first
    uint32_t n = 0;
    for (; t[n]; ++n) if (n >= 31 || !token_char(t[n], dot) || (!n && (t[n] == '_' || t[n] == '-' || t[n] == '.'))) return false;
    return n != 0;
}
// Keys of a key=value text into a small record.
struct Fields {
    uint32_t values[9]; bool have[9];
    const char* const* names; uint32_t count, hex;  // hex: bit i, names[i] is "0x" hex (the base's addresses)
    char a[32], b[32];                          // two text fields (base: profile, generation; package: name, machine)
    char list[128];                             // services=
    const char *a_key, *b_key;
};
void visit(void* context, const char* key, const char* value) {
    Fields& f = *static_cast<Fields*>(context);
    for (uint32_t i = 0; i != f.count; ++i)
        if (same(key, f.names[i])) { f.have[i] = f.hex >> i & 1u ? hex(value, f.values[i]) : number(value, f.values[i]); return; }
    char* dest = same(key, f.a_key) ? f.a : same(key, f.b_key) ? f.b : same(key, "services") ? f.list : 0;
    if (!dest) return;
    const uint32_t room = dest == f.list ? sizeof(f.list) : sizeof(f.a);
    uint32_t n = 0;
    while (value[n] && n + 1 < room) { dest[n] = value[n]; ++n; }
    dest[n] = 0;
}
void clear(Fields& f, const char* const* names, uint32_t count, const char* a, const char* b) {
    for (uint32_t i = 0; i != 9; ++i) { f.values[i] = 0; f.have[i] = false; }
    f.names = names; f.count = count; f.hex = 0; f.a[0] = f.b[0] = f.list[0] = 0; f.a_key = a; f.b_key = b;
}
bool before(const Package& a, const Package& b) {   // machines by order, then extensions by order
    const bool ea = a.kind == kind_extension, eb = b.kind == kind_extension;
    if (ea != eb) return eb;
    return a.order < b.order;
}
// The display order: kind (Kind order: synths, drums, samplers, effects, players, extensions), then name
// ignoring case, then folder.
char lower(char c) { return c >= 'A' && c <= 'Z' ? char(c + ('a' - 'A')) : c; }
int compare_text(const char* a, const char* b, bool fold) {
    while (*a && (fold ? lower(*a) == lower(*b) : *a == *b)) { ++a; ++b; }
    const unsigned char x = (unsigned char)(fold ? lower(*a) : *a), y = (unsigned char)(fold ? lower(*b) : *b);
    return x == y ? 0 : x < y ? -1 : 1;
}
bool sooner(const Page& p, const Package& a, const Package& b) {
    if (a.kind != b.kind) return a.kind < b.kind;
    const int c = compare_text(text_at(p, a.name), text_at(p, b.name), true);
    return c ? c < 0 : compare_text(text_at(p, a.id), text_at(p, b.id), false) < 0;
}
// list = the sorted rows the filter shows. fresh: filter_on takes what is on now; else it keeps what it listed
// (a row turned off stays until the filter changes).
// The selection stays on its package while listed; on the first row (or none, or unlisted): the first row.
void relist(Page& p, bool fresh) {
    const bool top = !p.listed || p.selected == p.list[0];
    uint32_t n = 0;
    for (uint32_t r = 0; r != p.shown; ++r) {
        Package& m = p.packages[p.sorted[r]];
        const uint8_t had = fresh ? flag_on : flag_listed;
        const bool in = p.filter == filter_all || (p.filter == filter_on ? (m.flags & had) != 0 : m.kind + 1u == p.filter);
        m.flags = uint8_t(in ? m.flags | flag_listed : m.flags & ~flag_listed);
        if (in) p.list[n++] = p.sorted[r];
    }
    p.listed = n;
    if (top || p.selected >= p.count || !(p.packages[p.selected].flags & flag_listed)) p.selected = n ? p.list[0] : no_row;
}
// A toggle: the last result line no longer applies (nor a note: the line shows the status again).
void toggled(Page& p) { if (p.view == View::list) p.line = line_none; }
}

uint32_t position(const Page& p) {
    for (uint32_t r = 0; r != p.listed; ++r) if (p.list[r] == p.selected) return r;
    return 0;
}

uint32_t on_count(const Page& p) {
    uint32_t n = 0;
    for (uint32_t r = 0; r != p.listed; ++r) n += p.packages[p.list[r]].flags & flag_on ? 1u : 0u;
    return n;
}

void set_filter(Page& p, uint32_t filter) { p.filter = filter > filter_on ? filter_all : filter; relist(p, true); }

uint32_t kind_count(const Page& p, uint32_t kind) {
    uint32_t n = 0;
    for (uint32_t r = 0; r != p.shown; ++r) n += p.packages[p.sorted[r]].kind == kind ? 1u : 0u;
    return n;
}

uint32_t next_filter(const Page& p, uint32_t f, bool back) {
    for (uint32_t i = 0; i != filter_on + 1; ++i) {
        f = back ? (f == filter_all || f > filter_on ? filter_on : f - 1) : f >= filter_on ? filter_all : f + 1;
        if (f == filter_all || f == filter_on || kind_count(p, f - 1)) return f;
    }
    return filter_all;
}

bool all_on(const Page& p) {
    for (uint32_t r = 0; r != p.listed; ++r) if (!(p.packages[p.list[r]].flags & flag_on)) return false;
    return p.listed != 0;
}

const char* filter_name(uint32_t f) {
    static const char* const names[filter_on + 1] = {"All", "Synths", "Drums", "Samplers", "Effects", "Players",
                                                     "Extensions", "On only"};
    return f <= filter_on ? names[f] : "";
}

uint32_t decimal(char* out, uint32_t v) {
    static const uint32_t tens[10] = {1000000000u, 100000000u, 10000000u, 1000000u, 100000u, 10000u, 1000u, 100u, 10u, 1u};
    uint32_t n = 0;
    for (uint32_t k = 0; k != 10; ++k) {
        char d = '0';
        while (v >= tens[k]) { v -= tens[k]; ++d; }
        if (d != '0' || n || k == 9) out[n++] = d;
    }
    out[n] = 0;
    return n;
}

void reset(Page& p) {
    p.view = View::reading; p.count = p.shown = 0; p.selected = no_row; p.request = request_none;
    p.filter = filter_all; p.listed = 0;
    p.profile[0] = p.generation[0] = 0;
    p.limit = p.expanded_limit = p.base_bytes = p.service_count = 0;
    p.pool_used = 1; p.pool[0] = 0;              // offset 0: the empty text
    p.line = line_none; p.text[0] = 0;
    p.saved_missing = 0; p.unknown_ok = false;
}

const char* text_at(const Page& p, uint16_t offset) { return offset < pool_bytes ? p.pool + offset : ""; }

void say(Page& p, Line line, const char* a, const char* b, const char* c) {
    char* at = p.text;
    const char* end = p.text + sizeof(p.text) - 1;
    put(at, end, a); put(at, end, b); put(at, end, c);
    *at = 0;
    p.line = line;
}

Effect key(Page& p, uint32_t k, bool pressed) {
    const bool turn = (k & key_turn) != 0;      // a knob turn's row (key_ui turn_knobs)
    k = as_play(k & ~key_turn);                 // a knob push is PLAY
    if (k == key_shift) { p.shift = pressed; return effect_none; }
    if (!pressed) return effect_none;
    if (k == key_stop) {
        if (!p.shift) return effect_leave;
        p.unknown_ok = !p.unknown_ok;           // unknown bases (SHIFT+STOP; was the status row)
        say(p, line_info, p.unknown_ok ? "Unknown mod bases allowed for the next build." : "Unknown mod bases refused.");
        return effect_redraw;
    }
    if (k == key_minus || k == key_plus) {
        if (turn && p.shift) {                  // SHIFT + turn: the filter, one step per click
            set_filter(p, next_filter(p, p.filter, k == key_minus));
            return effect_redraw;
        }
        if (!p.listed) return effect_none;
        const uint32_t at = position(p), last = p.listed - 1;
        const uint32_t next = p.shift && !turn ? (k == key_plus ? last : 0) :     // SHIFT + - / +: first / last
                              k == key_plus ? (at == last ? 0 : at + 1) : (at == 0 ? last : at - 1);
        p.selected = p.list[next];
        return effect_redraw;
    }
    if (k != key_play) return effect_none;
    if (p.shift) {                              // every listed row on, or (all on) off: pending only
        if (!p.listed) return effect_none;
        const bool on = !all_on(p);
        for (uint32_t r = 0; r != p.listed; ++r) {
            Package& m = p.packages[p.list[r]];
            m.flags = uint8_t(on ? m.flags | flag_on : m.flags & ~flag_on);
        }
        toggled(p);
        return effect_redraw;
    }
    if (p.selected >= p.count) return effect_none;
    Package& m = p.packages[p.selected];
    m.flags ^= flag_on;
    toggled(p);
    return effect_redraw;
}

bool generation_token(const char* t) {
    uint32_t n = 0;
    for (; t[n]; ++n) {
        const char c = t[n];
        const bool alnum = (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9');
        if (n >= 31 || !(alnum || c == '_' || c == '-' || c == '.') || (!n && !alnum)) return false;
    }
    return n != 0;
}

int compare_generation(const char* a, const char* b) {
    // `<n>.<n>.<n>-<hex>`: numbers compared as numbers up to the '-', then the text.
    while (*a && *a != '-' && *b && *b != '-') {
        uint32_t x = 0, y = 0, nx = 0, ny = 0;
        while (a[nx] >= '0' && a[nx] <= '9' && nx < 9) x = x * 10u + uint32_t(a[nx++] - '0');
        while (b[ny] >= '0' && b[ny] <= '9' && ny < 9) y = y * 10u + uint32_t(b[ny++] - '0');
        if (x != y) return x > y ? 1 : -1;
        if (!nx || !ny) { if (*a != *b) return *a > *b ? 1 : -1; ++a; ++b; continue; }
        a += nx; b += ny;
        if (*a == '.' && *b == '.') { ++a; ++b; continue; }
        if (*a != *b) return (*a == '.') ? 1 : (*b == '.') ? -1 : (*a > *b ? 1 : -1);
    }
    while (*a && *a == *b) { ++a; ++b; }
    return *a == *b ? 0 : (unsigned char)*a > (unsigned char)*b ? 1 : -1;
}

bool parse_base(Page& p, const char* text, uint32_t length) {
    static const char* const names[9] = {"module_bytes", "code", "data", "state", "runtime_code", "runtime_data",
                                         "runtime_state", "module_base", "expanded_module_base"};
    Fields f;
    clear(f, names, 9, "profile", "generation");
    f.hex = 3u << 7;
    if (store::parse_key_values(text, length, visit, &f) < 0) return false;
    for (uint32_t i = 0; i != 7; ++i) if (!f.have[i]) return false;
    if (!f.values[0] || !token(f.a, true) || !token(f.b, true)) return false;
    p.limit = f.values[0];
    // The 4 MiB layout's region (the assembler's mod_patch::layout checks the layout itself)
    const uint32_t x = f.values[8], below = f.values[7] - x;
    p.expanded_limit = f.have[7] && f.have[8] && x && x < f.values[7] && below <= 0x10000000u ? p.limit + below : 0u;
    p.base_bytes = f.values[1] + f.values[2] + f.values[3] + f.values[4] + f.values[5] + f.values[6];
    for (uint32_t i = 0; i != 32; ++i) { p.profile[i] = f.a[i]; p.generation[i] = f.b[i]; }
    return true;
}

bool parse_services(Page& p, const char* text, uint32_t length) {
    p.service_count = 0;
    uint32_t i = 0;
    while (i < length) {
        uint32_t end = i;
        while (end < length && text[end] != '\n') ++end;
        if (text[i] != '#' && end > i) {
            char line[128];
            uint32_t n = 0;
            for (uint32_t k = i; k < end && n + 1 < sizeof(line); ++k) if (text[k] != '\r') line[n++] = text[k];
            line[n] = 0;
            char* word = line;
            uint32_t bytes = 0, fields = 0;
            char* name = 0;
            for (char* at = line;; ++at) {
                const bool last = !*at;
                if (*at != ' ' && !last) continue;
                *at = 0;
                if (!name) name = word;
                else if (*word) {
                    char* eq = word;
                    while (*eq && *eq != '=') ++eq;
                    if (*eq) {
                        *eq = 0;
                        uint32_t v = 0;
                        if ((same(word, "code") || same(word, "data") || same(word, "state")) && number(eq + 1, v)) {
                            bytes += v; ++fields;
                        }
                    }
                }
                if (last) break;
                word = at + 1;
            }
            if (!name || !*name || fields != 3 || p.service_count == max_services) return false;
            const uint16_t at = keep(p, name, 31);
            if (at == 0xffffu) return false;
            p.service_names[p.service_count] = at;
            p.service_bytes[p.service_count++] = bytes;
        }
        i = end + 1;
    }
    return true;
}

int add_package(Page& p, const char* folder) {
    if (p.count == max_packages || same(folder, "base")) return -1;
    const bool extension = folder[0] == 'e' && folder[1] == 'x' && folder[2] == 't' && folder[3] == '-';
    const char* id = extension ? folder + 4 : folder;
    if (!store::valid_id(id)) return -1;
    const uint16_t at = keep(p, id, 31);
    if (at == 0xffffu) return -1;
    Package& m = p.packages[p.count];
    m.id = at; m.name = 0; m.kind = extension ? kind_extension : kind_other; m.flags = 0;
    m.order = 0; m.services = 0; m.bytes = 0;
    return int(p.count++);
}

bool parse_package(Page& p, uint32_t index, const char* text, uint32_t length) {
    static const char* const names[5] = {"order", "objects", "code", "data", "state"};
    static const char* const kinds[6] = {"synth", "drum", "sample", "effect", "player", "extension"};
    if (index >= p.count) return false;
    Package& m = p.packages[index];
    Fields f;
    clear(f, names, 5, "name", "machine");
    if (store::parse_key_values(text, length, visit, &f) < 0) return false;
    for (uint32_t i = 0; i != 5; ++i) if (!f.have[i]) return false;
    uint32_t kind = kind_other;
    for (uint32_t i = 0; i != 6; ++i) if (same(f.b, kinds[i])) kind = i;
    if (kind == kind_other || (kind == kind_extension) != (m.kind == kind_extension)) return false;
    uint32_t services = 0;
    if (!same(f.list, "-")) {
        for (char* word = f.list;;) {
            char* at = word;
            while (*at && *at != ',') ++at;
            const bool more = *at == ',';
            *at = 0;
            for (uint32_t s = 0; s != p.service_count; ++s)
                if (same(word, text_at(p, p.service_names[s]))) services |= 1u << s;
            if (!more) break;
            word = at + 1;
        }
    }
    const uint16_t name = keep(p, *f.a ? f.a : text_at(p, m.id), name_chars);
    if (name == 0xffffu) return false;
    m.name = name; m.kind = uint8_t(kind); m.order = uint16_t(f.values[0] > 0xffffu ? 0xffffu : f.values[0]);
    m.services = services;
    m.bytes = f.values[2] + f.values[3] + f.values[4] + 8u * f.values[1];
    m.flags |= flag_read;
    uint32_t r = p.shown;                       // insert in build order
    while (r && before(m, p.packages[p.rows[r - 1]])) { p.rows[r] = p.rows[r - 1]; --r; }
    p.rows[r] = uint8_t(index);
    r = p.shown;                                // and in the display order (one insertion: bounded per read)
    while (r && sooner(p, m, p.packages[p.sorted[r - 1]])) { p.sorted[r] = p.sorted[r - 1]; --r; }
    p.sorted[r] = uint8_t(index);
    ++p.shown;
    relist(p, false);
    return true;
}

uint32_t used(const Page& p) {
    uint32_t total = p.base_bytes, services = 0;
    for (uint32_t r = 0; r != p.shown; ++r) {
        const Package& m = p.packages[p.rows[r]];
        if (!(m.flags & flag_on)) continue;
        total += m.bytes;
        services |= m.services;
    }
    for (uint32_t s = 0; s != p.service_count; ++s) if (services >> s & 1u) total += p.service_bytes[s];
    return total;
}

uint32_t shared(const Page& p) {
    uint32_t services = 0, total = 0;
    for (uint32_t r = 0; r != p.shown; ++r) {
        const Package& m = p.packages[p.rows[r]];
        if (m.flags & flag_on) services |= m.services;
    }
    for (uint32_t s = 0; s != p.service_count; ++s) if (services >> s & 1u) total += p.service_bytes[s];
    return total;
}

uint32_t memory_limit(const Page& p) { return p.expanded && p.expanded_limit ? p.expanded_limit : p.limit; }

uint32_t memory_row(const Page& p, bool read) {
    using namespace xyf::settings;
    if (p.expanded) return memory_expanded;     // it can always go back to standard
    if (!p.limit) return read ? memory_unavailable : memory_reading;
    return p.expanded_limit ? memory_standard : memory_unavailable;
}

const char* flip_memory(Page& p, bool read) {
    using namespace xyf::settings;
    const uint32_t row = memory_row(p, read);
    if (row == memory_reading) return "Still reading the mod packages.";
    if (row == memory_unavailable) return "These mod packages have no 4 MiB layout.";
    p.expanded = !p.expanded;
    toggled(p);
    return 0;
}

uint32_t chosen(const Page& p, bool extensions) {
    uint32_t n = 0;
    for (uint32_t r = 0; r != p.shown; ++r) {
        const Package& m = p.packages[p.rows[r]];
        if ((m.flags & flag_on) && (m.kind == kind_extension) == extensions) ++n;
    }
    return n;
}

const char* build_refusal(const Page& p) {
    if (p.view == View::reading) return "Still reading the mod packages.";
    if (!p.limit) return "No mod packages to build from.";
    const uint32_t machines = chosen(p, false), extensions = chosen(p, true);
    if (!machines) return "Turn on at least one machine first.";
    if (machines > max_machines) return "At most 64 machines in one build.";
    if (extensions > max_extensions) return "At most 8 extensions in one build.";
    if (p.expanded && !p.expanded_limit) return "4 MiB mod memory needs newer mod packages: set it to 2 MiB.";
    if (used(p) > memory_limit(p)) return "Too big for the mods memory: turn some mods off.";
    return 0;
}

bool changed(const Page& p) {
    if (p.saved_missing || p.expanded != p.expanded_saved) return true;
    for (uint32_t r = 0; r != p.shown; ++r) {
        const uint8_t f = p.packages[p.rows[r]].flags;
        if (!(f & flag_on) != !(f & flag_saved)) return true;
    }
    return false;
}

const char* save_refusal(const Page& p) {
    if (p.view == View::reading) return "Still reading the mod packages.";
    return chosen(p, false) + chosen(p, true) ? build_refusal(p) : 0;
}

uint32_t request(const Page& p, const char* base, bool unverified, bool expanded, uint8_t which, char* name,
                 uint32_t name_capacity, uint8_t* out, uint32_t capacity) {
    {
        char* at = name;
        const char* end = name + name_capacity - 1;
        uint32_t count = 0;
        for (uint32_t r = 0; r != p.shown; ++r) count += p.packages[p.rows[r]].flags & which ? 1u : 0u;
        put(at, end, "Mods: ");
        put_number(at, end, count);
        *at = 0;
    }
    uint32_t n = 0;
    bool fits = true;
    auto add = [&](const char* s) { for (; *s; ++s) { if (n == capacity) { fits = false; return; } out[n++] = uint8_t(*s); } };
    auto end_field = [&]() { if (n == capacity) fits = false; else out[n++] = 0; };
    add(base); end_field();
    add(p.profile); end_field();
    add(p.generation); end_field();
    for (uint32_t pass = 0; pass != 2; ++pass) {
        bool first = true;
        for (uint32_t r = 0; r != p.shown; ++r) {
            const Package& m = p.packages[p.rows[r]];
            if (!(m.flags & which) || (m.kind == kind_extension) != (pass == 1)) continue;
            if (!first) add(",");
            add(text_at(p, m.id));
            first = false;
        }
        end_field();
    }
    add(name); end_field();
    const uint32_t flags = (unverified ? 1u : 0u) | (expanded ? 2u : 0u);
    for (uint32_t i = 0; i != 4; ++i) { if (n == capacity) fits = false; else out[n++] = uint8_t(flags >> (8 * i)); }
    return fits ? n : 0;
}

Effect progress_key(Progress& r, uint32_t k, bool pressed) {
    if (!pressed || as_play(k & ~key_turn) != key_stop || r.cancel) return effect_none;   // only STOP: cancel (the job's own cleanup runs)
    if (r.job_commit || r.save_stage) {         // the library commit cannot be cancelled: said once (and
        if (r.note[0]) return effect_none;      // neither can installing content or saving)
        refuse_cancel(r);
        return effect_redraw;
    }
    r.cancel = 1;
    return effect_redraw;
}

void refuse_cancel(Progress& r) {
    r.cancel = 0;
    char* at = r.note;
    put(at, r.note + sizeof(r.note) - 1, r.save_stage == 1 ? "Cannot cancel now: installing content." :
                                         r.save_stage ? "Cannot cancel now: saving." :
                                         "Cannot cancel now: it is being added to the library.");
    *at = 0;
}

void reading_line(Page& p, uint32_t at) {
    char a[12], tail[24];
    tail[0] = ' '; tail[1] = 'o'; tail[2] = 'f'; tail[3] = ' ';
    decimal(a, at);
    const uint32_t n = 4 + decimal(tail + 4, p.count);
    tail[n] = '.'; tail[n + 1] = '.'; tail[n + 2] = '.'; tail[n + 3] = 0;
    say(p, line_info, "Reading mod packages: ", a, tail);
}

void start_refused(Page& p, uint32_t status, uint32_t missing) {
    using namespace xyf::usbp;
    const char* why = status == s_busy ? "busy (a job, an upload or a save is running): try again." :
                      status == s_link ? "the storage side did not answer." :
                      status == s_bad_request ? "the request was refused (names or counts)." :
                      status == s_state && missing == missing_ids_taken ?
                          "both mod slots are in use by the default and boot-once." :
                      status == s_state ? "XY Forever cannot start a job now." :
                      status == s_unsupported ? "the mod packages need a newer XY Forever." : 0;
    if (status == s_not_found)
        why = missing == 1 ? "the base firmware has no os.ldr." :
              missing == 2 ? "content.sha256 missing beside the base." :
              missing == 3 ? "content/op1_factory.json is missing." :
              missing == 4 ? "the mod base package is missing." :
              missing == 5 ? "the mod packages are in an older layout: push them again." : "an input is missing.";
    if (why) { say(p, line_error, "Build failed: ", why); return; }
    char d[12];
    decimal(d, status);
    say(p, line_error, "Build failed: status ", d, ".");
}

void job_ended(Page& p, const char* name, uint32_t phase, uint32_t error, uint32_t status, uint32_t debris) {
    if (phase == uint32_t(mj::Phase::done)) {   // committed; an assembled set is never installed
        say(p, line_done, "Built: ", name, " (content not installed yet).");
        return;
    }
    if (phase == uint32_t(mj::Phase::cancelled)) {
        say(p, line_info, debris ? "Cancelled. Leftovers stay in tmp/." : "Cancelled. Nothing was kept.");
        return;
    }
    using E = mj::Error;
    const E e = E(error);
    const char* why = 0;
    if (error == 21) why = "the storage side stopped answering.";                       // usb_service job_link_error
    else if (e == E::content && status == uint32_t(xyf_core::mod_content::Status::catalog))
        why = "the content catalog is not the official one.";
    else if (e == E::content && status == uint32_t(xyf_core::mod_content::Status::official))
        why = "a mod would replace official content.";
    else if ((e == E::patch || e == E::base) && status == uint32_t(xyf_core::mod_patch::Status::unverified)) {
        say(p, line_error, "Unknown mod base: SHIFT+STOP on the Mods page allows it.");
        return;
    } else if (e == E::patch && status == uint32_t(xyf_core::mod_patch::Status::stock))
        why = "the base firmware is not the stock image the mods need.";
    else if (e == E::link && status == uint32_t(xyf_core::mod_link::Status::capacity))
        why = "the mods do not fit in the mods memory.";
    else if (e == E::open || e == E::read || e == E::truncated) why = "a package file is missing or unreadable.";
    else if (e == E::list || e == E::unlisted || e == E::sha256) why = "a package file is damaged.";
    else if (e == E::write || e == E::rename || e == E::verify) why = "writing to storage failed.";
    if (why) { say(p, line_error, "Build failed: ", why); return; }
    static const char* const errors[21] = {"none", "argument", "path", "region", "open", "read", "truncated", "size",
                                           "list", "unlisted", "sha256", "info", "base", "link", "patch", "content",
                                           "write", "rename", "verify", "cancelled", "image"};
    char line[40];
    char* at = line;
    put(at, line + sizeof(line) - 1, error < 21 ? errors[error] : "error");
    put(at, line + sizeof(line) - 1, " ");
    put_number(at, line + sizeof(line) - 1, status);
    *at = 0;
    say(p, line_error, "Build failed: ", line, ".");
}

// The stages' shares of the bar and typical device times: the simulator's stage times of real one- and
// seven-mod builds (the published set; tests/test_sim_mods_page.py PublishedPage prints them), averaged as
// shares and scaled to the device's 9 s and 12 s; the near-instant stages (start, content, finish, commit)
// get a small floor so the bar visibly passes them.
namespace {
//                                  start  inputs    link  patch content   write   check finish commit
const uint32_t shares[bar_stages] = {10000, 230000, 150000, 20000, 10000, 280000, 260000, 20000, 20000};
const uint16_t times[bar_stages] =  {  100,   2400,   1600,   200,   100,   2900,   2700,   200,   200};
}
uint32_t stage_share(uint32_t i) { return i < bar_stages ? shares[i] : 0; }
uint32_t stage_ms(uint32_t i) { return i < bar_stages ? times[i] : 0; }

uint32_t progress(Progress& p, uint32_t ms) {
    const bool building = !p.save || !p.save_stage;
    const uint32_t stage = !building ? 8u + p.save_stage : p.job_commit ? 8u : p.job_phase > 7 ? 7u : p.job_phase;
    const uint32_t total = p.job_total ? p.job_total : 1u, done = p.job_done > total ? total : p.job_done;
    const uint32_t step = stage << 16 | (done > 0xffffu ? 0xffffu : done);
    if (step != p.bar_step) { p.bar_step = step; p.bar_since = ms; }
    uint32_t base = 0, share, typical_ms;
    if (building) {
        for (uint32_t i = 0; i != stage; ++i) base += shares[i];
        share = shares[stage]; typical_ms = times[stage];
        if (p.save) {                           // the build's part of a save's bar
            const uint32_t span = p.save & save_build ? save_build_part : 0u;
            base = ui::scale(span, base, bar_full); share = ui::scale(span, share, bar_full);
        }
    } else {                                    // installing content (files), then saving
        const uint32_t from = p.save & save_build ? save_build_part : 0u;
        const uint32_t mid = p.save & save_build ? save_content_end : save_content_only;
        base = p.save_stage == 1 ? from : mid;
        share = p.save_stage == 1 ? mid - from : bar_full - mid;
        typical_ms = p.save_stage == 1 ? 3000u : 1500u;
    }
    const uint32_t from = base + ui::scale(share, done, total);
    const uint32_t to = done < total ? base + ui::scale(share, done + 1, total) : from;
    // Within the step: t / (t + typical step time, at least 0.5 s) of its width, which keeps moving and stays
    // below its end.
    const uint32_t step_ms = ui::scale(typical_ms, 1, total), t = ms - p.bar_since;
    const uint32_t typical = step_ms < 500 ? 500 : step_ms;
    const uint32_t value = from + ui::scale(to - from, t, t + typical);
    if (value > p.bar) p.bar = value > bar_full ? bar_full : value;
    return p.bar;
}

const char* kind_name(uint32_t kind) {
    static const char* const names[7] = {"synth", "drum", "sample", "effect", "player", "extension", ""};
    return kind < 7 ? names[kind] : "";
}

const char* phase_text(uint32_t phase) {
    static const char* const names[11] = {"Starting", "Reading inputs", "Linking", "Patching", "Content", "Writing",
                                          "Checking", "Finishing", "Done", "Failed", "Cancelled"};
    return phase < 11 ? names[phase] : "";
}
} }
