#include "json.hpp"
// Keep the pinned parser implementation unchanged. Its only required libc helpers
// are these small ASCII operations; optional numeric getters are never called.
namespace {
int ascii_digit(int c) { return c >= '0' && c <= '9'; }
int ascii_hex(int c) { return ascii_digit(c) || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F'); }
int ascii_compare(const char* a, const char* b) {
    while (*a && *a == *b) { ++a; ++b; }
    return int(static_cast<unsigned char>(*a)) - int(static_cast<unsigned char>(*b));
}
}
#undef isdigit
#undef isxdigit
#define isdigit ascii_digit
#define isxdigit ascii_hex
#define strcmp ascii_compare
#define XYF_TINY_JSON_FREESTANDING
#include "../third_party/tiny-json/tiny-json.c"
#undef XYF_TINY_JSON_FREESTANDING
#undef isdigit
#undef isxdigit
#undef strcmp

namespace xyf_core { namespace json {
bool same(const char* a, const char* b) { return a && b && !ascii_compare(a, b); }
const json_t* get(const json_t* object, const char* name) {
    return object && object->type == JSON_OBJ ? json_getProperty(object, name) : 0;
}
bool unsigned_value(const json_t* p, uint32_t& out) {
    if (!p || p->type != JSON_INTEGER) return false;
    const char* s = p->u.value;
    uint32_t n = 0, v = 0;
    while (s[n]) {
        const char c = s[n++];
        if (c < '0' || c > '9' || n > 10 || v > 429496729u || (v == 429496729u && c > '5')) return false;
        v = v * 10u + uint32_t(c - '0');
    }
    if (!n) return false;
    out = v; return true;
}
bool bool_value(const json_t* p, bool& out) {
    if (!p || p->type != JSON_BOOLEAN) return false;
    if (same(p->u.value, "true")) { out = true; return true; }
    if (same(p->u.value, "false")) { out = false; return true; }
    return false;
}
bool text_value(const json_t* p, uint32_t maximum) {
    if (!p || p->type != JSON_TEXT) return false;
    uint32_t n = 0;
    for (const char* s = p->u.value; *s; ++s)
        if (uint8_t(*s) < 0x20 || uint8_t(*s) > 0x7e || ++n > maximum) return false;
    return true;
}
Document parse(const char* source, uint32_t bytes, Workspace& work, const char* expected) {
    Document d = {0, size};
    if (!source || !bytes || bytes > document_bytes) return d;
    // This is a representation restriction, not another JSON syntax validator:
    // tiny-json's Unicode decoder is lossy. Do not silently turn identifiers into '?'.
    bool escaped = false;
    for (uint32_t i = 0; i != bytes; ++i) {
        const char c = source[i];
        if (!c) { d.status = syntax; return d; }
        if (escaped && c == 'u') { d.status = unicode; return d; }
        escaped = !escaped && c == '\\';
        work.text[i] = c;
    }
    work.text[bytes] = 0;
    d.root = json_create(work.text, work.properties, property_count);
    d.status = syntax;
    if (!d.root || d.root->type != JSON_OBJ) return d;
    // Bounded iterative traversal: the parser itself uses no recursion. Check all
    // object duplicates, so even fields a future version consumes stay unambiguous.
    const json_t* pending[max_depth];
    uint32_t level = 0, visited = 0;
    const json_t* p = d.root;
    while (p) {
        if (++visited > property_count) { d.status = capacity; return d; }
        if (p->name) {
            uint32_t n = 0;
            while (p->name[n]) if (++n > key_bytes) { d.status = capacity; return d; }
        }
        if (p->type == JSON_TEXT && !text_value(p)) { d.status = value; return d; }
        if (p->type == JSON_OBJ || p->type == JSON_ARRAY) {
            if (level == max_depth) { d.status = depth; return d; }
            uint32_t children = 0;
            for (const json_t* a = p->u.c.child; a; a = a->sibling) {
                if (++children > (p->type == JSON_OBJ ? field_count + 2 : list_count)) {
                    d.status = capacity; return d;
                }
                if (p->type == JSON_OBJ) {
                    if (!a->name) { d.status = syntax; return d; }
                    for (const json_t* b = p->u.c.child; b != a; b = b->sibling)
                        if (same(a->name, b->name)) { d.status = duplicate; return d; }
                }
            }
            if (p->u.c.child) { pending[level++] = p->sibling; p = p->u.c.child; continue; }
        }
        p = p->sibling;
        while (!p && level) p = pending[--level];
    }
    const json_t* s = get(d.root, "schema");
    if (!text_value(s) || !same(s->u.value, expected)) { d.status = schema; return d; }
    uint32_t v = 0;
    if (!unsigned_value(get(d.root, "schema_version"), v) || v != 1) { d.status = version; return d; }
    d.status = ok; return d;
}
namespace {
struct Out {
    char* data; uint32_t bytes, capacity; bool fits;
    void text(const char* s) { while (*s) { if (bytes == capacity) fits = false; else data[bytes++] = *s; ++s; } }
    void number(uint32_t n) {
        static const uint32_t powers[] = {1000000000u,100000000u,10000000u,1000000u,100000u,10000u,1000u,100u,10u,1u};
        bool started = false;
        for (uint32_t i = 0; i != 10; ++i) {
            char c = '0'; while (n >= powers[i]) { n -= powers[i]; ++c; }
            if (c != '0' || started || i == 9) { char s[2] = {c,0}; text(s); started = true; }
        }
    }
};
bool word(const char* value, const char* words) {
    for (const char* p = words; *p;) {
        uint32_t i = 0;
        while (p[i] && p[i] != ' ' && value[i] && p[i] == value[i]) ++i;
        if ((!p[i] || p[i] == ' ') && !value[i]) return true;
        while (*p && *p != ' ') ++p;
        if (*p) ++p;
    }
    return false;
}
bool key_ok(const char* key) {
    uint32_t n = 0;
    for (; key[n]; ++n) {
        const char c = key[n];
        if (n == key_bytes || !((c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_')) return false;
    }
    return n != 0;
}
bool identifier(const char* s, bool entry, bool empty) {
    if (!*s) return empty;
    if (!entry && !((*s >= 'a' && *s <= 'z') || (*s >= '0' && *s <= '9'))) return false;
    for (; *s; ++s)
        if (!((*s >= 'a' && *s <= 'z') || (*s >= '0' && *s <= '9') || *s == '_' || *s == '-' || (!entry && *s == '.'))) return false;
    return true;
}
bool digest(const char* s) {
    for (uint32_t i = 0; i != 64; ++i)
        if (!((s[i] >= '0' && s[i] <= '9') || (s[i] >= 'a' && s[i] <= 'f'))) return false;
    return !s[64];
}
enum Shape { text_shape, number_shape, address_shape, null_shape, list_shape, pair_shape };
Status scalar_size(const json_t* p, const char* key, uint32_t& size, Shape& shape) {
    const bool number = word(key, "bytes machines module_base module_bytes stock_bytes edits objects extensions code data state runtime_code runtime_data runtime_state catalog_engine_at catalog_fx_at catalog_seq_at stock_records reserved_bytes heap_fill_base heap_fill_bytes expanded_module_base order link presets");
    const bool optional = word(key, "engine effect player page");
    size = 0; shape = text_shape;
    if (number || optional) {
        uint32_t v = 0;
        if (optional && p->type == JSON_NULL) { shape = null_shape; size = 1; return ok; }
        if (!unsigned_value(p, v)) return type;
        shape = word(key, "module_base heap_fill_base expanded_module_base") ? address_shape : number_shape;
        if (shape == address_shape) { size = 10; return ok; }
    } else if (word(key, "services features claims accessors")) {
        shape = list_shape;
        if (p->type != JSON_ARRAY) return type;
        uint32_t count = 0;
        for (const json_t* item = p->u.c.child; item; item = item->sibling) {
            if (!text_value(item) || !item->u.value[0]) return value;
            if (count++) ++size;
            for (const char* text = item->u.value; *text; ++text) {
                if (*text == ',') return value;
                ++size;
            }
        }
        if (!count) size = 1;
        return size <= value_bytes ? ok : value;
    } else if (same(key, "catalog_number")) {
        if (p->type == JSON_NULL) { shape = null_shape; size = 1; return ok; }
        shape = pair_shape;
        if (p->type != JSON_ARRAY) return type;
        uint32_t count = 0, v = 0;
        for (const json_t* item = p->u.c.child; item; item = item->sibling) {
            if (++count > 2 || !unsigned_value(item, v)) return value;
            if (count == 2) ++size;
            for (const char* text = item->u.value; *text; ++text) ++size;
        }
        return count == 2 ? ok : value;
    } else if (same(key, "cpu_peak_percent")) {
        if (p->type != JSON_INTEGER && p->type != JSON_REAL) return type;
    } else if (!text_value(p)) return type;
    for (const char* text = p->u.value; *text; ++text) if (++size > value_bytes) return value;
    return ok;
}
Status scalar(Out& out, const json_t* p, const char* key) {
    uint32_t size = 0; Shape shape;
    const Status status = scalar_size(p, key, size, shape);
    if (status != ok) return status;
    if (shape == null_shape) out.text("-");
    else if (shape == address_shape) {
        uint32_t v = 0; unsigned_value(p, v);
        char digits[11] = {'0','x'};
        for (uint32_t i = 0; i != 8; ++i) digits[i + 2] = "0123456789abcdef"[(v >> (28 - 4 * i)) & 15u];
        digits[10] = 0; out.text(digits);
    } else if (shape == list_shape || shape == pair_shape) {
        uint32_t count = 0;
        for (const json_t* item = p->u.c.child; item; item = item->sibling) {
            if (count++) out.text(",");
            out.text(item->u.value);
        }
        if (!count) out.text("-");
    } else out.text(p->u.value);
    return ok;
}
Status field(Out& out, const json_t* p, const char* key) {
    if (!p) return value;
    out.text(key); out.text("=");
    const uint32_t start = out.bytes;
    const Status status = scalar(out, p, key);
    if (out.bytes - start > value_bytes) return value;
    out.text("\n"); return status;
}
}
Status package_field_size(const json_t* p, uint32_t& bytes) {
    if (!p || !key_ok(p->name)) return capacity;
    Shape shape;
    return scalar_size(p, p->name, bytes, shape);
}
Status package_text(const Document& doc, char* out, uint32_t capacity_, uint32_t& bytes) {
    bytes = 0;
    if (doc.status != ok) return doc.status;
    Out o = {out,0,capacity_,true}; uint32_t fields = 0;
    for (const json_t* p = doc.root->u.c.child; p; p = p->sibling) {
        if (same(p->name,"schema") || same(p->name,"schema_version")) continue;
        if (++fields > field_count || !key_ok(p->name)) return capacity;
        const Status status = field(o,p,p->name); if (status != ok) return status;
    }
    if (!get(doc.root,"type")) return value;
    bytes = o.bytes; return o.fits ? ok : capacity;
}
Status config_text(const Document& doc, char* out, uint32_t capacity_, uint32_t& bytes) {
    bytes = 0;
    if (doc.status != ok) return doc.status;
    const json_t* desired = get(doc.root,"desired");
    if (!desired || desired->type != JSON_OBJ) return type;
    Out o = {out,0,capacity_,true}; o.text("format=1\n");
    const char* const keys[] = {"base","profile","generation","memory"};
    for (uint32_t i = 0; i != 4; ++i) {
        const json_t* p = get(desired,keys[i]);
        if (!text_value(p,31)) return value;
        if (i < 3 && !identifier(p->u.value,i == 0,i != 0)) return value;
        if (same(keys[i],"memory") && !word(p->u.value,"standard expanded")) return value;
        o.text(keys[i]); o.text("="); o.text(p->u.value); o.text("\n");
    }
    bool unverified = false;
    if (!bool_value(get(desired,"unverified"),unverified)) return type;
    o.text(unverified ? "unverified=1\n" : "unverified=0\n");
    const json_t* built = get(doc.root,"built");
    if (!built || (built->type != JSON_NULL && built->type != JSON_OBJ)) return type;
    if (built->type == JSON_OBJ) {
        const char* const names[] = {"base","generation","set","count","memory","slot","sha256"};
        for (uint32_t i = 0; i != 7; ++i) {
            const json_t* p = get(built,names[i]);
            if (i == 2 || i == 3) { uint32_t v; if (!unsigned_value(p,v)) return type; }
            else if (!text_value(p,i == 6 ? 64 : 31)) return value;
        }
        if (!word(get(built,"memory")->u.value,"standard expanded") ||
            !word(get(built,"slot")->u.value,"mods-a mods-b")) return value;
        if (!identifier(get(built,"base")->u.value,true,false) ||
            !identifier(get(built,"generation")->u.value,false,false) || !digest(get(built,"sha256")->u.value)) return value;
        o.text("built="); o.text(get(built,"base")->u.value); o.text(" "); o.text(get(built,"generation")->u.value); o.text(" ");
        uint32_t set = 0, count = 0;
        unsigned_value(get(built,"set"),set); unsigned_value(get(built,"count"),count);
        if (count > list_count) return value;
        char digits[9];
        for (uint32_t i = 0; i != 8; ++i) digits[i] = "0123456789abcdef"[(set >> (28 - 4 * i)) & 15u];
        digits[8] = 0; o.text(digits); o.text(" "); o.number(count);
        if (same(get(built,"memory")->u.value,"expanded")) o.text(" expanded");
        o.text("\nslot="); o.text(get(built,"slot")->u.value); o.text("\nstamp="); o.text(get(built,"sha256")->u.value); o.text("\n");
    }
    o.text("mods=");
    const json_t* mods = get(desired,"mods");
    if (!mods || mods->type != JSON_ARRAY) return type;
    uint32_t count = 0;
    for (const json_t* p = mods->u.c.child; p; p = p->sibling) {
        if (++count > list_count || !text_value(p,31) || !p->u.value[0]) return value;
        for (const char* s = p->u.value; *s; ++s)
            if (!((*s >= 'a' && *s <= 'z') || (*s >= '0' && *s <= '9') || *s == '_' || *s == '-' || *s == '.')) return value;
        for (const json_t* prior = mods->u.c.child; prior != p; prior = prior->sibling)
            if (same(prior->u.value,p->u.value)) return duplicate;
        if (count != 1) o.text(",");
        o.text(p->u.value);
    }
    o.text("\n"); bytes = o.bytes;
    return o.fits ? ok : capacity;
}
const char* message(Status s) {
    switch (s) {
    case ok:return "ok"; case size:return "JSON document size"; case syntax:return "JSON syntax or property limit";
    case depth:return "JSON nesting limit"; case duplicate:return "duplicate JSON field or selection";
    case schema:return "unsupported JSON schema"; case version:return "unsupported JSON schema version";
    case type:return "JSON field type"; case value:return "JSON field value"; case capacity:return "JSON capacity";
    case unicode:return "Unicode escapes unsupported in native JSON";
    }
    return "JSON error";
}
} }
