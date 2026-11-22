#ifndef XYF_CONFIG_HPP
#define XYF_CONFIG_HPP
#include <stdint.h>
// The saved configuration ("Open-XY", configured like a BIOS: #197, a flip on the Mods page is saved at once,
// Continue converges to it and boots). Freestanding, header only, pure (host-tested through
// the simulator; processor B's xyf_mods_io reads and applies it, usb_service's config job writes it).
// Functions are static: an inline (weak) function is a register call on Blackfin (the builder's call audit).
//
// The legacy record and the in-memory view of the JSON record (key=value lines, LF):
//   format=1
//   base=<library entry id>           the stock firmware the mods are built on
//   profile=<p> generation=<g>         the mod package set the selection is from (one line each)
//   unverified=0|1                     the user allowed an unknown base (SHIFT+PLAY on the Mods status row)
//   built=<base> <generation> <set> <count>   what the Open-XY firmware in slot= was made from (set: the sum
//                                      of the CRC-32 of every name of the selection, 8 hex: order-free)
//   slot=mods-a|mods-b                 the library entry holding it (empty: never built)
//   stamp=<64 hex>                     that entry's manifest SHA-256 (its os.ldr) when it was built
//   memory=standard|expanded           Mod memory (Settings): expanded, the 4 MiB layout (mods.apply flag
//                                      bit 1); absent or anything else: standard. built= then ends " expanded".
//   safe=0|1                           safe mode ("Boot without mods"): the base boots with its official content,
//                                      the rest is kept for "Restore Open-XY"; absent: 0, also with no slot or mods
//   mods=<name>,<name>,...             the mods switched on: machine folders, then ext-<id>; empty: none (last)
// Save replaces config.json with config.json.part and config.json.old. Legacy
// config.txt and its rollback copy remain readable when both native names are absent.
namespace xyf { namespace config {
static const char path[] = "/yaffs2/xy-forever/config.json";
static const char old_path[] = "/yaffs2/xy-forever/config.json.old";
static const char part_path[] = "/yaffs2/xy-forever/config.json.part";
static const char legacy_path[] = "/yaffs2/xy-forever/config.txt";
static const char legacy_old_path[] = "/yaffs2/xy-forever/config.old";
static const char* const names[] = {"config.json", "config.json.old", "config.txt", "config.old"};
static const char* const paths[] = {path, old_path, legacy_path, legacy_old_path};
static const uint32_t path_count = sizeof(names) / sizeof(names[0]), max_text = 2048;
static inline bool native_record(uint32_t index) { return index < 2; }
enum State : uint32_t { unread = 0, none = 1, present = 2, failed = 3 };
struct Record {
    uint32_t state, count, unverified, expanded, safe;
    char base[32], profile[32], generation[32], built[100], slot[8], stamp[65];
};
static inline bool same(const char* a, const char* b) {
    while (*a && *a == *b) ++a, ++b;
    return *a == *b;
}
static inline bool same_n(const char* a, const char* b, uint32_t n) {
    uint32_t i = 0;
    while (i != n && a[i] == b[i]) ++i;
    return i == n;
}
static inline void clear(Record& r, uint32_t state) {
    r.state = state;
    r.count = r.unverified = r.expanded = r.safe = 0;
    r.base[0] = r.profile[0] = r.generation[0] = r.built[0] = r.slot[0] = r.stamp[0] = 0;
}
// The value of key on its own line (key=value), or 0; length in n.
static inline const char* value(const char* t, uint32_t length, const char* key, uint32_t& n) {
    for (uint32_t at = 0; at < length;) {
        uint32_t k = 0;
        while (key[k] && at + k < length && t[at + k] == key[k]) ++k;
        uint32_t end = at;
        while (end < length && t[end] != '\n') ++end;
        if (!key[k] && at + k < length && t[at + k] == '=') {
            n = end - at - k - 1;
            return t + at + k + 1;
        }
        at = end + 1;
    }
    n = 0;
    return 0;
}
static inline bool supported_format(const char* t, uint32_t length) {
    for (uint32_t at = 0; at < length;) {
        uint32_t n = 0;
        const char* f = value(t + at, length - at, "format", n);
        if (!f) break;
        at = uint32_t(f - t) + n + 1;
        if (n && f[n - 1] == '\r') --n;
        if (n != 1 || f[0] != '1') return false;
    }
    return true;
}
static inline void field(const char* t, uint32_t length, const char* key, char* out, uint32_t capacity) {
    uint32_t n = 0, k = 0;
    const char* v = value(t, length, key, n);
    for (; v && k != n && k + 1 < capacity; ++k) out[k] = v[k];
    out[k] = 0;
}
static inline void parse(const char* t, uint32_t length, Record& r) {
    clear(r, present);
    field(t, length, "base", r.base, sizeof(r.base));
    field(t, length, "profile", r.profile, sizeof(r.profile));
    field(t, length, "generation", r.generation, sizeof(r.generation));
    field(t, length, "built", r.built, sizeof(r.built));
    field(t, length, "slot", r.slot, sizeof(r.slot));
    field(t, length, "stamp", r.stamp, sizeof(r.stamp));
    uint32_t n = 0;
    const char* u = value(t, length, "unverified", n);
    r.unverified = u && n == 1 && u[0] == '1';
    const char* e = value(t, length, "memory", n);
    r.expanded = e && n == 8 && same_n(e, "expanded", 8);
    const char* s = value(t, length, "safe", n);
    r.safe = s && n == 1 && s[0] == '1';
    const char* m = value(t, length, "mods", n);
    for (uint32_t i = 0; m && i != n; ++i)
        if ((i == 0 || m[i - 1] == ',') && m[i] != ',') ++r.count;
    if (!same(r.slot, "mods-a") && !same(r.slot, "mods-b")) r.slot[0] = r.built[0] = r.stamp[0] = 0;
    if (!r.slot[0] || !r.count) r.safe = 0;              // nothing to restore
}
// Is name (an extension with its "ext-" prefix) in the record's mods= line?
static inline bool listed(const char* t, uint32_t length, const char* prefix, const char* name) {
    uint32_t n = 0;
    const char* m = value(t, length, "mods", n);
    for (uint32_t at = 0; m && at < n;) {
        uint32_t end = at;
        while (end < n && m[end] != ',') ++end;
        uint32_t k = at, i = 0;
        for (; prefix[i] && k < end && m[k] == prefix[i]; ++i) ++k;
        if (!prefix[i]) {
            uint32_t j = 0;
            while (name[j] && k < end && m[k] == name[j]) ++k, ++j;
            if (!name[j] && k == end) return true;
        }
        at = end + 1;
    }
    return false;
}
// The stamp of a library entry: its info.txt sha256= (64 lowercase hex), or "" (out: 65 bytes).
static inline void stamp_of(const char* t, uint32_t length, char* out) {
    uint32_t n = 0;
    const char* v = value(t, length, "sha256", n);
    out[0] = 0;
    if (!v || n != 64) return;
    for (uint32_t i = 0; i != 64; ++i) {
        if (!((v[i] >= '0' && v[i] <= '9') || (v[i] >= 'a' && v[i] <= 'f'))) {
            out[0] = 0;
            return;
        }
        out[i] = v[i];
    }
    out[64] = 0;
}
// Text writer (bounded; ok false when it did not fit).
struct Out {
    char* at;
    char* end;
    bool ok;
};
static inline void put(Out& o, const char* s) {
    while (*s) {
        if (o.at == o.end) {
            o.ok = false;
            return;
        }
        *o.at++ = *s++;
    }
}
static inline void put_hex(Out& o, uint32_t v) {
    static const char digits[] = "0123456789abcdef";
    char d[9];
    for (uint32_t i = 0; i != 8; ++i) d[i] = digits[(v >> (28 - 4 * i)) & 15u];
    d[8] = 0;
    put(o, d);
}
static inline void put_number(Out& o, uint32_t v) {     // no division (no libgcc)
    static const uint32_t tens[4] = {1000u, 100u, 10u, 1u};
    char d[6];
    uint32_t n = 0;
    if (v > 9999u) v = 9999u;
    for (uint32_t k = 0; k != 4; ++k) {
        char c = '0';
        while (v >= tens[k]) {
            v -= tens[k];
            ++c;
        }
        if (c != '0' || n || k == 3) d[n++] = c;
    }
    d[n] = 0;
    put(o, d);
}
// built= for a selection (set: the order-free sum of its names' CRC-32; count: how many; " expanded"
// after it in the 4 MiB layout, so a change of Mod memory rebuilds).
static inline void built_text(char* out, uint32_t capacity, const char* base, const char* generation, uint32_t set,
                              uint32_t count, bool expanded) {
    Out o = {out, out + capacity - 1, true};
    put(o, base);
    put(o, " ");
    put(o, generation);
    put(o, " ");
    put_hex(o, set);
    put(o, " ");
    put_number(o, count);
    if (expanded) put(o, " expanded");
    *o.at = 0;
    if (!o.ok) out[0] = 0;
}
}}
#endif
