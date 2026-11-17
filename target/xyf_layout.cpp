#include "xyf_store.hpp"
// open-xy layout.json reader; separate so state/info-only builds do not link it.
namespace xyf { namespace store {
namespace {
bool equal(const char* a, const char* b) {
    while (*a && *a == *b) ++a, ++b;
    return *a == *b;
}
void zero(void* p, uint32_t n) { for (uint32_t i = 0; i != n; ++i) static_cast<char*>(p)[i] = 0; }
struct Json {
    const char* s; uint32_t n, i;
    bool ws() { while (i < n && (s[i] == ' ' || s[i] == '\n' || s[i] == '\r' || s[i] == '\t')) ++i; return i < n; }
    bool eat(char c) { if (!ws() || s[i] != c) return false; ++i; return true; }
    // Kept fields are printable ASCII; skipped strings accept every JSON escape. Long
    // unknown keys are consumed whole (with a truncated name), not refused for their size.
    bool string(char* out, uint32_t capacity, bool key = false) {
        if (!eat('"')) return false;
        uint32_t k = 0;
        while (i < n && s[i] != '"') {
            uint32_t c = uint8_t(s[i++]);
            if (c < 0x20) return false;
            if (c == '\\') {
                if (i == n) return false;
                c = uint8_t(s[i++]);
                if (c == 'u') {
                    c = 0;
                    for (uint32_t h = 0; h != 4; ++h) {
                        if (i == n) return false;
                        const char d = s[i++];
                        uint32_t v;
                        if (d >= '0' && d <= '9') v = uint32_t(d - '0');
                        else if (d >= 'a' && d <= 'f') v = uint32_t(d - 'a' + 10);
                        else if (d >= 'A' && d <= 'F') v = uint32_t(d - 'A' + 10);
                        else return false;
                        c = c * 16u + v;
                    }
                } else if (c == 'b') c = 8;
                else if (c == 'f') c = 12;
                else if (c == 'n') c = 10;
                else if (c == 'r') c = 13;
                else if (c == 't') c = 9;
                else if (c != '"' && c != '\\' && c != '/') return false;
            }
            if (out) {
                if (c < 0x20 || c > 0x7e) { if (!key) return false; c = '?'; }
                if (k + 1 < capacity) out[k++] = char(c);
                else if (!key) return false;
            }
        }
        if (i == n) return false;
        ++i;
        if (out) out[k] = 0;
        return true;
    }
    // Unknown numbers need only be valid JSON; interpreted numbers must fit int32_t.
    bool number(int32_t* out = 0) {
        if (!ws()) return false;
        const bool negative = s[i] == '-';
        if (negative && ++i == n) return false;
        if (s[i] < '0' || s[i] > '9') return false;
        uint32_t v = 0;
        const bool leading_zero = s[i] == '0';
        const uint32_t first = i, last = negative ? 8u : 7u;
        while (i < n && s[i] >= '0' && s[i] <= '9') {
            const uint32_t digit = uint32_t(s[i++] - '0');
            if (out) {
                if (v > 214748364u || (v == 214748364u && digit > last)) return false;
                v = v * 10u + digit;
            }
        }
        if (leading_zero && i - first != 1) return false;
        if (i < n && s[i] == '.') {
            if (out) return false;
            const uint32_t start = ++i;
            while (i < n && s[i] >= '0' && s[i] <= '9') ++i;
            if (i == start) return false;
        }
        if (i < n && (s[i] == 'e' || s[i] == 'E')) {
            if (out) return false;
            ++i;
            if (i < n && (s[i] == '+' || s[i] == '-')) ++i;
            const uint32_t start = i;
            while (i < n && s[i] >= '0' && s[i] <= '9') ++i;
            if (i == start) return false;
        }
        if (out) *out = negative ? int32_t(0u - v) : int32_t(v);
        return true;
    }
    bool literal(const char* word) { ws(); for (; *word; ++word, ++i) if (i >= n || s[i] != *word) return false; return true; }
    bool skip(uint32_t depth) {
        if (depth > max_depth || !ws()) return false;
        const char c = s[i];
        if (c == '"') return string(0, 0);
        if (c == '{' || c == '[') {
            const char close = c == '{' ? '}' : ']';
            ++i;
            if (eat(close)) return true;
            for (;;) {
                if (c == '{' && (!string(0, 0) || !eat(':'))) return false;
                if (!skip(depth + 1)) return false;
                if (eat(close)) return true;
                if (!eat(',')) return false;
            }
        }
        if (c == 't') return literal("true");
        if (c == 'f') return literal("false");
        if (c == 'n') return literal("null");
        return number();
    }
};
bool machine(Json& j, Machine& m) {
    if (!j.eat('{')) return false;
    bool has_number = false, has_page = false;
    if (j.eat('}')) return false;
    for (;;) {
        char key[16];
        if (!j.string(key, sizeof(key), true) || !j.eat(':')) return false;
        int32_t v;
        if (equal(key, "engine") || equal(key, "effect") || equal(key, "player")) {
            if (has_number || !j.number(&v) || v < 0 || v > 255) return false;
            m.kind = equal(key, "engine") ? machine_engine : equal(key, "effect") ? machine_effect : machine_player;
            m.number = int16_t(v); has_number = true;
        } else if (equal(key, "page")) {
            if (has_page || !j.number(&v) || v < 0 || v > 1023) return false;
            m.page = int16_t(v); has_page = true;
        } else if (!j.skip(2)) return false;
        if (j.eat('}')) break;
        if (!j.eat(',')) return false;
    }
    return has_number && has_page;
}
bool ids(Json& j, char (*out)[max_machine_id + 1], uint32_t capacity, uint32_t& count) {
    if (!j.eat('[')) return false;
    if (j.eat(']')) return true;
    for (;;) {
        if (count == capacity || !j.string(out[count], max_machine_id + 1) || !valid_id(out[count])) return false;
        for (uint32_t k = 0; k != count; ++k) if (equal(out[k], out[count])) return false;
        ++count;
        if (j.eat(']')) return true;
        if (!j.eat(',')) return false;
    }
}
}
bool parse_layout(const char* json, uint32_t length, Layout& layout) {
    zero(&layout, sizeof(layout));
    if (length > max_layout) return false;
    Json j = {json, length, 0};
    if (!j.eat('{')) return false;
    bool format_ok = false, has_profile = false, has_machines = false, has_extensions = false;
    if (!j.eat('}')) for (;;) {
        char key[24];
        if (!j.string(key, sizeof(key), true) || !j.eat(':')) return false;
        if (equal(key, "format")) {
            char value[24];
            if (format_ok || !j.string(value, sizeof(value)) || !equal(value, "opxy-layout/1")) return false;
            format_ok = true;
        } else if (equal(key, "profile")) {
            if (has_profile || !j.string(layout.profile, sizeof(layout.profile)) ||
                (!equal(layout.profile, "opxy-1.2.1") && !equal(layout.profile, "opxy-1.2.0"))) return false;
            has_profile = true;
        } else if (equal(key, "selected")) {
            if (layout.has_selected || !ids(j, layout.selected, max_machines, layout.selected_count)) return false;
            layout.has_selected = true;
        } else if (equal(key, "extensions")) {
            if (has_extensions || !ids(j, layout.extensions, max_extensions, layout.extension_count)) return false;
            has_extensions = true;
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
    if (!format_ok || !has_profile || !has_machines || j.i != length) return false;
    for (uint32_t k = 0; k != layout.selected_count; ++k) {
        bool found = false;
        for (uint32_t m = 0; m != layout.count; ++m) if (equal(layout.selected[k], layout.machines[m].id)) found = true;
        if (!found) return false;
    }
    return true;
}
} }
