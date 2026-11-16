#include "xyf_store.hpp"
// open-xy layout.json reader (declared in xyf_store.hpp), kept in its own
// object so builds that only need state.txt/info.txt (the USB service) do
// not link it.
namespace xyf { namespace store {
namespace {
bool equal(const char* a, const char* b) {
    while (*a && *a == *b) ++a, ++b;
    return *a == *b;
}
void zero(void* p, uint32_t n) { for (uint32_t i = 0; i != n; ++i) static_cast<char*>(p)[i] = 0; }
}
// ---- layout.json: bounded JSON subset reader (objects, arrays, strings,
// integers, true/false/null). Only the fields XY Forever needs are kept.
namespace {
struct Json {
    const char* s; uint32_t n, i;
    bool ws() { while (i < n && (s[i] == ' ' || s[i] == '\n' || s[i] == '\r' || s[i] == '\t')) ++i; return i < n; }
    bool eat(char c) { if (!ws() || s[i] != c) return false; ++i; return true; }
    bool peek(char c) { return ws() && s[i] == c; }
    // String into out (escapes other than \" \\ \/ rejected: not needed here).
    bool string(char* out, uint32_t capacity) {
        if (!eat('"')) return false;
        uint32_t k = 0;
        while (i < n && s[i] != '"') {
            char c = s[i++];
            if (c == '\\') { if (i >= n) return false; c = s[i++]; if (c != '"' && c != '\\' && c != '/') return false; }
            if (uint8_t(c) < 0x20) return false;
            if (k + 1 < capacity) out[k++] = c; else return false;
        }
        if (i >= n) return false;
        ++i;
        if (capacity) out[k] = 0;
        return true;
    }
    bool integer(int32_t& out) {
        if (!ws()) return false;
        bool negative = false;
        if (s[i] == '-') { negative = true; ++i; }
        if (i >= n || s[i] < '0' || s[i] > '9') return false;
        int32_t v = 0;
        while (i < n && s[i] >= '0' && s[i] <= '9') { if (v > 100000) return false; v = v * 10 + (s[i++] - '0'); }
        if (i < n && (s[i] == '.' || s[i] == 'e' || s[i] == 'E')) return false;
        out = negative ? -v : v;
        return true;
    }
    bool literal(const char* word) { ws(); for (; *word; ++word, ++i) if (i >= n || s[i] != *word) return false; return true; }
    // Skip any value, nesting bounded by depth.
    bool skip(uint32_t depth) {
        if (depth > max_depth || !ws()) return false;
        const char c = s[i];
        if (c == '"') { char sink[1]; return string_skip(sink); }
        if (c == '{' || c == '[') {
            const char close = c == '{' ? '}' : ']';
            ++i;
            if (eat(close)) return true;
            for (;;) {
                if (c == '{') { char sink[1]; if (!string_skip(sink) || !eat(':')) return false; }
                if (!skip(depth + 1)) return false;
                if (eat(close)) return true;
                if (!eat(',')) return false;
            }
        }
        if (c == 't') return literal("true");
        if (c == 'f') return literal("false");
        if (c == 'n') return literal("null");
        int32_t v; return integer(v);
    }
    bool string_skip(char*) {
        if (!eat('"')) return false;
        while (i < n && s[i] != '"') { if (s[i] == '\\') ++i; ++i; }
        if (i >= n) return false;
        ++i;
        return true;
    }
};

bool machine(Json& j, Machine& m) {
    // {"engine"|"effect": int, "page": int} (other keys skipped)
    if (!j.eat('{')) return false;
    bool has_number = false, has_page = false;
    if (j.eat('}')) return false;
    for (;;) {
        char key[16];
        if (!j.string(key, sizeof(key)) || !j.eat(':')) return false;
        int32_t v;
        if (equal(key, "engine") || equal(key, "effect")) {
            if (has_number || !j.integer(v) || v < 0 || v > 255) return false;
            m.kind = equal(key, "engine") ? machine_engine : machine_effect;
            m.number = int16_t(v); has_number = true;
        } else if (equal(key, "page")) {
            if (has_page || !j.integer(v) || v < 0 || v > 1023) return false;
            m.page = int16_t(v); has_page = true;
        } else if (!j.skip(2)) return false;
        if (j.eat('}')) break;
        if (!j.eat(',')) return false;
    }
    return has_number && has_page;
}
}

bool parse_layout(const char* json, uint32_t length, Layout& layout) {
    zero(&layout, sizeof(layout));
    if (length > max_layout) return false;
    Json j = {json, length, 0};
    if (!j.eat('{')) return false;
    bool format_ok = false, has_machines = false;
    if (!j.eat('}')) for (;;) {
        char key[24];
        if (!j.string(key, sizeof(key)) || !j.eat(':')) return false;
        if (equal(key, "format")) {
            char value[24];
            if (!j.string(value, sizeof(value))) return false;
            format_ok = equal(value, "opxy-layout/1");
        } else if (equal(key, "profile")) {
            if (!j.string(layout.profile, sizeof(layout.profile))) return false;
        } else if (equal(key, "machines")) {
            if (has_machines || !j.eat('{')) return false;
            has_machines = true;
            if (!j.eat('}')) for (;;) {
                if (layout.count == max_machines) return false;
                Machine& m = layout.machines[layout.count];
                if (!j.string(m.id, sizeof(m.id)) || !valid_id(m.id) || !j.eat(':') || !machine(j, m)) return false;
                for (uint32_t k = 0; k != layout.count; ++k) if (equal(layout.machines[k].id, m.id)) return false;
                ++layout.count;
                if (j.eat('}')) break;
                if (!j.eat(',')) return false;
            }
        } else if (!j.skip(1)) return false;
        if (j.eat('}')) break;
        if (!j.eat(',')) return false;
    }
    j.ws();
    return format_ok && j.i == length;
}
} }
