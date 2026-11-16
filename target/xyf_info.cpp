#include "xyf_store.hpp"
// info.txt reader (declared in xyf_store.hpp), in its own object so builds that
// only read/write state slots (the USB service) do not link it.
namespace xyf { namespace store {
namespace {
bool equal(const char* a, const char* b) {
    while (*a && *a == *b) ++a, ++b;
    return *a == *b;
}
void copy(char* out, uint32_t capacity, const char* in) {
    uint32_t i = 0;
    for (; in[i] && i + 1 < capacity; ++i) out[i] = in[i];
    out[i] = 0;
}
void zero(void* p, uint32_t n) { for (uint32_t i = 0; i != n; ++i) static_cast<char*>(p)[i] = 0; }
bool decimal(const char* s, uint32_t& out) {
    if (!*s) return false;
    uint32_t v = 0;
    for (; *s; ++s) {
        if (*s < '0' || *s > '9' || v > 429496729u || (v == 429496729u && *s > '5')) return false;
        v = v * 10 + uint32_t(*s - '0');
    }
    out = v;
    return true;
}
bool hex8(const char* s, uint32_t& out) {
    uint32_t v = 0, n = 0;
    for (; *s; ++s, ++n) {
        const char c = *s;
        const uint32_t d = c >= '0' && c <= '9' ? uint32_t(c - '0') : c >= 'a' && c <= 'f' ? uint32_t(c - 'a' + 10) : 16;
        if (d == 16 || n == 8) return false;
        v = v << 4 | d;
    }
    if (n != 8) return false;
    out = v;
    return true;
}
void info_visit(void* context, const char* key, const char* value) {
    Info& f = *static_cast<Info*>(context);
    if (equal(key, "name")) copy(f.name, sizeof(f.name), value);
    else if (equal(key, "version")) copy(f.version, sizeof(f.version), value);
    else if (equal(key, "kind"))
        f.kind = equal(value, "official") ? kind_official : equal(value, "open-xy") ? kind_open_xy :
                 equal(value, "custom") ? kind_custom : kind_unknown;
    else if (equal(key, "bytes")) f.has_bytes = decimal(value, f.bytes);
    else if (equal(key, "crc32")) f.has_crc32 = hex8(value, f.crc32);
    else if (equal(key, "generation")) f.has_generation = *value != 0;
}
}

bool parse_info(const char* text, uint32_t length, Info& info) {
    zero(&info, sizeof(info));
    return parse_key_values(text, length, info_visit, &info) >= 0;
}
} }
