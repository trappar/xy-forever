#include "xyf_store.hpp"
namespace xyf { namespace store {
namespace {
bool id_char(char c) { return (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_' || c == '-'; }
bool key_char(char c) { return (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_'; }
bool space(char c) { return c == ' ' || c == '\t'; }
bool equal(const char* a, const char* b) {
    while (*a && *a == *b) ++a, ++b;
    return *a == *b;
}
void copy(char* out, uint32_t capacity, const char* in) {
    uint32_t i = 0;
    for (; in[i] && i + 1 < capacity; ++i) out[i] = in[i];
    out[i] = 0;
}
void zero(void* p, uint32_t n) {
    for (uint32_t i = 0; i != n; ++i) static_cast<char*>(p)[i] = 0;
}
// Decimal (no sign) or 8 lowercase hex digits for crc32; false on overflow/junk.
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
}

bool valid_id(const char* id) {
    uint32_t n = 0;
    for (; id[n]; ++n)
        if (n == max_id || !id_char(id[n])) return false;
    return n != 0;
}

int parse_key_values(const char* text, uint32_t length, Visit visit, void* context) {
    if (length > max_text) return -1;
    int pairs = 0;
    uint32_t i = 0;
    while (i < length) {
        uint32_t end = i;
        while (end < length && text[end] != '\n') ++end;
        uint32_t a = i, b = end;
        if (b > a && text[b - 1] == '\r') --b;
        i = end + 1;
        while (a < b && space(text[a])) ++a;
        if (a == b || text[a] == '#') continue;
        char key[max_key + 1], value[max_value + 1];
        uint32_t k = 0;
        bool ok = true;
        while (a < b && key_char(text[a])) {
            if (k == max_key) {
                ok = false;
                break;
            }
            key[k++] = text[a++];
        }
        key[k] = 0;
        while (ok && a < b && space(text[a])) ++a;
        if (!ok || !k || a == b || text[a] != '=') continue;
        ++a;
        while (a < b && space(text[a])) ++a;
        while (b > a && space(text[b - 1])) --b;
        if (b - a > max_value) continue;
        uint32_t v = 0;
        for (; a < b; ++a) value[v++] = text[a];
        value[v] = 0;
        visit(context, key, value);
        ++pairs;
    }
    return pairs;
}

namespace {
void state_visit(void* context, const char* key, const char* value) {
    State& s = *static_cast<State*>(context);
    if (equal(key, "default")) copy(s.default_id, sizeof(s.default_id), valid_id(value) ? value : "");
    else if (equal(key, "once")) copy(s.once, sizeof(s.once), valid_id(value) ? value : "");
    else if (equal(key, "stage2")) copy(s.stage2, sizeof(s.stage2), value);
    else if (equal(key, "stage2_previous")) copy(s.stage2_previous, sizeof(s.stage2_previous), value);
    else if (equal(key, "stage2_tries")) {
        uint32_t v;
        if (decimal(value, v)) s.stage2_tries = v;
    }
}

struct Writer {
    char* out;
    uint32_t capacity, at;
    bool ok;
    void put(const char* s) {
        for (; *s; ++s) {
            if (at + 1 >= capacity) {
                ok = false;
                return;
            }
            out[at++] = *s;
        }
    }
    void field(const char* key, const char* value) {
        if (*value) {
            put(key);
            put("=");
            put(value);
            put("\n");
        }
    }
};
}

bool parse_state(const char* text, uint32_t length, State& state) {
    zero(&state, sizeof(state));
    return parse_key_values(text, length, state_visit, &state) >= 0;
}

uint32_t write_state(const State& s, char* out, uint32_t capacity) {
    Writer w = {out, capacity, 0, capacity != 0};
    w.field("default", s.default_id);
    w.field("once", s.once);
    w.field("stage2", s.stage2);
    w.field("stage2_previous", s.stage2_previous);
    if (s.stage2_tries) {
        // Decimal without division (no libgcc helpers in the cold build).
        static const uint32_t powers[10] = {1000000000u, 100000000u, 10000000u, 1000000u, 100000u,
                                            10000u,      1000u,      100u,      10u,      1u};
        char digits[11];
        uint32_t v = s.stage2_tries, n = 0;
        for (uint32_t p = 0; p != 10; ++p) {
            char d = '0';
            while (v >= powers[p]) {
                v -= powers[p];
                ++d;
            }
            if (n || d != '0' || p == 9) digits[n++] = d;
        }
        digits[n] = 0;
        w.field("stage2_tries", digits);
    }
    if (!w.ok) return 0;
    out[w.at] = 0;
    return w.at;
}

}}
