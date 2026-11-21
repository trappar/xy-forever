#include "xyf_library.hpp"
namespace xyf { namespace library {
namespace {
int compare(const char* a, const char* b) {
    while (*a && *a == *b) ++a, ++b;
    return int(static_cast<unsigned char>(*a)) - int(static_cast<unsigned char>(*b));
}
void copy(char* out, uint32_t capacity, const char* in) {
    uint32_t i = 0;
    for (; in[i] && i + 1 < capacity; ++i) out[i] = in[i];
    out[i] = 0;
}
bool append(char* out, uint32_t& at, const char* s) {
    for (; *s; ++s) { if (at + 1 >= detail::path_capacity) return false; out[at++] = *s; }
    out[at] = 0;
    return true;
}
// Byte-wise swap: a struct assignment would compile to memcpy (not linked cold).
void swap(void* a, void* b, uint32_t n) {
    char* x = static_cast<char*>(a); char* y = static_cast<char*>(b);
    for (uint32_t i = 0; i != n; ++i) { const char t = x[i]; x[i] = y[i]; y[i] = t; }
}
}

namespace detail {
const char root[] = "/yaffs2/xy-forever";

void reset(Library& out) {
    for (uint32_t i = 0; i != sizeof(out); ++i) reinterpret_cast<char*>(&out)[i] = 0;
}
bool path(char* out, const char* a, const char* b, const char* c, const char* d) {
    uint32_t at = 0;
    out[0] = 0;
    return append(out, at, a) && append(out, at, b) && append(out, at, c) && append(out, at, d);
}
void add_entry(Library& out, const char* name) {
    if (!compare(name, ".") || !compare(name, "..")) return;
    if (!store::valid_id(name) || out.count == max_entries) { ++out.skipped; return; }
    for (uint32_t i = 0; i != out.count; ++i) if (!compare(out.entries[i].id, name)) { ++out.skipped; return; }
    copy(out.entries[out.count].id, sizeof(out.entries[0].id), name);
    ++out.count;
}
void sort(Library& out) {                          // insertion sort by id (count <= 16)
    for (uint32_t i = 1; i < out.count; ++i)
        for (uint32_t j = i; j && compare(out.entries[j].id, out.entries[j - 1].id) < 0; --j)
            swap(&out.entries[j], &out.entries[j - 1], sizeof(Entry));
}
void apply_state(Library& out, state::Source source, const uint8_t* text, uint32_t length) {
    state::Choice choice;                               // fold into the running choice
    const char* from = reinterpret_cast<const char*>(&out.state);
    char* to = reinterpret_cast<char*>(&choice.state);
    for (uint32_t i = 0; i != sizeof(out.state); ++i) to[i] = from[i];
    choice.source = state::Source(out.state_source);
    choice.seq = out.state_seq;
    state::consider(choice, source, reinterpret_cast<const char*>(text), length);
    if (choice.source == state::Source(out.state_source) && choice.seq == out.state_seq && out.has_state) return;
    if (choice.source == state::source_none) return;
    from = reinterpret_cast<const char*>(&choice.state);
    to = reinterpret_cast<char*>(&out.state);
    for (uint32_t i = 0; i != sizeof(out.state); ++i) to[i] = from[i];
    out.state_source = choice.source;
    out.state_seq = choice.seq;
    out.has_state = true;
}
void apply_info(Entry& e, const uint8_t* text, uint32_t length) {
    e.has_info = store::parse_info(reinterpret_cast<const char*>(text), length, e.info);
}
void finish(Library& out, Entry& e) {
    e.machines = e.has_info && e.info.has_machines ? e.info.machines : 0;
    e.usable = e.image_present && !e.content_missing && !e.invalid_record;
    if (!e.usable) ++out.skipped;
    if (!e.has_info || !e.info.name[0]) copy(e.info.name, sizeof(e.info.name), e.id);
}
}

int32_t find(const Library& library, const char* id) {
    if (!id || !*id) return -1;
    for (uint32_t i = 0; i != library.count; ++i) if (!compare(library.entries[i].id, id)) return int32_t(i);
    return -1;
}

Choice boot_choice(const Library& library, uint32_t& index) {
    const int32_t once = find(library, library.state.once);
    if (once >= 0 && library.entries[once].usable) { index = uint32_t(once); return Choice::once; }
    const int32_t fallback = find(library, library.state.default_id);
    if (fallback >= 0 && library.entries[fallback].usable) { index = uint32_t(fallback); return Choice::by_default; }
    index = 0;
    return Choice::menu;
}
} }
