#include "xyf_manifest.hpp"
#include "xyf_store.hpp"
// The strict info.txt reader and the dependency rules (xyf_manifest.hpp). The files.sha256
// reader and the recogniser are in their own objects so a build links only what it uses.
namespace xyf { namespace manifest {
namespace {
const char* const key_names[key_count] = {
    "type", "name", "version", "kind", "bytes", "crc32", "sha256", "content",
    "needs_os", "generation", "profile", "machine", "source", "licence", "machines"};
const char* const machine_names[] = {"synth", "drum", "sample", "effect", "player", "extension"};
const uint16_t bit_type = 1u << key_type;
// Keys each type takes and needs (010: the firmware and mod examples, the base line).
const uint16_t firmware_keys = uint16_t(
    1u << key_name | 1u << key_version | 1u << key_kind | 1u << key_bytes | 1u << key_crc32 | 1u << key_sha256 |
    1u << key_content | 1u << key_needs_os | 1u << key_generation | 1u << key_profile | 1u << key_source |
    1u << key_licence | 1u << key_machines);
const uint16_t base_keys = uint16_t(1u << key_name | 1u << key_version | 1u << key_profile | 1u << key_generation |
                                    1u << key_needs_os | 1u << key_source | 1u << key_licence | 1u << key_machines);
const uint16_t mod_keys = uint16_t(1u << key_name | 1u << key_version | 1u << key_machine | 1u << key_generation |
                                   1u << key_profile | 1u << key_source | 1u << key_licence);
const uint16_t firmware_needs = uint16_t(1u << key_name | 1u << key_bytes | 1u << key_crc32 | 1u << key_sha256);
const uint16_t base_needs = uint16_t(1u << key_profile | 1u << key_generation | 1u << key_needs_os);
const uint16_t mod_needs = uint16_t(1u << key_name | 1u << key_machine | 1u << key_generation);

bool space(char c) { return c == ' ' || c == '\t'; }
bool key_char(char c) { return (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_'; }
bool token_char(char c) { return (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '.' || c == '_' || c == '-'; }
void zero(void* p, uint32_t n) { for (uint32_t i = 0; i != n; ++i) static_cast<char*>(p)[i] = 0; }
// a[0..n) equals the NUL-terminated word.
bool same(const char* a, uint32_t n, const char* word) {
    uint32_t i = 0;
    for (; i != n; ++i) if (!word[i] || word[i] != a[i]) return false;
    return !word[i];
}
bool equal(const char* a, const char* b) {
    while (*a && *a == *b) ++a, ++b;
    return *a == *b;
}
bool equal32(const uint8_t* a, const uint8_t* b) {
    for (uint32_t i = 0; i != 32; ++i) if (a[i] != b[i]) return false;
    return true;
}
uint32_t nibble(char c) {
    return c >= '0' && c <= '9' ? uint32_t(c - '0') : c >= 'a' && c <= 'f' ? uint32_t(c - 'a' + 10) : 16;
}
bool hex_bytes(const char* s, uint32_t n, uint8_t* out, uint32_t count) {
    if (n != 2 * count) return false;
    for (uint32_t i = 0; i != count; ++i) {
        const uint32_t hi = nibble(s[2 * i]), lo = nibble(s[2 * i + 1]);
        if (hi == 16 || lo == 16) return false;
        out[i] = uint8_t(hi << 4 | lo);
    }
    return true;
}
bool number(const char* s, uint32_t n, uint32_t& out) {
    if (!n || n > 10 || (n > 1 && s[0] == '0')) return false;
    uint32_t v = 0;
    for (uint32_t i = 0; i != n; ++i) {
        const char c = s[i];
        if (c < '0' || c > '9' || v > 429496729u || (v == 429496729u && c > '5')) return false;
        v = v * 10 + uint32_t(c - '0');
    }
    out = v;
    return true;
}
char* text_field(Manifest& m, uint32_t key, uint32_t& capacity) {
    switch (key) {
    case key_name: capacity = sizeof(m.name); return m.name;
    case key_version: capacity = sizeof(m.version); return m.version;
    case key_content: capacity = sizeof(m.content); return m.content;
    case key_generation: capacity = sizeof(m.generation); return m.generation;
    case key_profile: capacity = sizeof(m.profile); return m.profile;
    case key_source: capacity = sizeof(m.source); return m.source;
    case key_licence: capacity = sizeof(m.licence); return m.licence;
    default: capacity = 0; return 0;
    }
}
// Stores one known key's value (non-empty, bytes already 0x20..0x7e); ok or the reason.
Status store_value(Manifest& m, uint32_t key, const char* v, uint32_t n) {
    switch (key) {
    case key_type:
        if (same(v, n, "firmware")) m.type = type_firmware;
        else if (same(v, n, "mod-base")) m.type = type_mod_base;
        else if (same(v, n, "mod")) m.type = type_mod;
        else if (same(v, n, "companion")) m.type = type_companion;
        else return unknown_type;
        return ok;
    case key_kind:
        if (same(v, n, "official")) m.kind = store::kind_official;
        else if (same(v, n, "open-xy")) m.kind = store::kind_open_xy;
        else if (same(v, n, "custom")) m.kind = store::kind_custom;
        else return bad_value;
        return ok;
    case key_machine:
        for (uint32_t i = 0; i != sizeof(machine_names) / sizeof(machine_names[0]); ++i)
            if (same(v, n, machine_names[i])) { m.machine = uint8_t(machine_synth + i); return ok; }
        return bad_value;
    case key_machines: return number(v, n, m.machines) && m.machines <= 65535u ? ok : bad_number;
    case key_bytes: return number(v, n, m.bytes) ? ok : bad_number;
    case key_crc32: {
        uint8_t b[4];
        if (!hex_bytes(v, n, b, 4)) return bad_hex;
        m.crc32 = uint32_t(b[0]) << 24 | uint32_t(b[1]) << 16 | uint32_t(b[2]) << 8 | b[3];
        return ok;
    }
    case key_sha256: return hex_bytes(v, n, m.sha256, 32) ? ok : bad_hex;
    case key_needs_os: return hex_bytes(v, n, m.needs_os, 32) ? ok : bad_hex;
    default: break;
    }
    uint32_t capacity;
    char* out = text_field(m, key, capacity);
    if (n >= capacity) return value_too_long;
    if (key == key_generation || key == key_profile) {     // path names under mods/ (010): tokens
        if (v[0] == '.' || v[0] == '-' || v[0] == '_') return bad_value;
        for (uint32_t i = 0; i != n; ++i) if (!token_char(v[i])) return bad_value;
    }
    for (uint32_t i = 0; i != n; ++i) out[i] = v[i];
    out[n] = 0;
    return ok;
}
Status fail(Manifest& m, Status status, uint32_t line, uint32_t key, uint32_t lines) {
    const uint8_t type = m.type;
    zero(&m, sizeof(m));
    m.status = status;
    m.type = type;
    m.line = uint16_t(line);
    m.key = uint8_t(key);
    m.lines = uint16_t(lines);
    return status;
}
uint32_t first_key(uint16_t bits) {
    for (uint32_t k = 0; k != key_count; ++k) if (bits >> k & 1u) return k;
    return key_unknown;
}
}

bool has(const Manifest& m, Key key) { return key < key_count && (m.keys >> key & 1u); }

Status parse(const char* text, uint32_t length, Manifest& m) {
    zero(&m, sizeof(m));
    m.key = key_unknown;
    if (length > max_text) return fail(m, too_large, 0, key_unknown, 0);
    if (!length || !text) return fail(m, empty, 0, key_unknown, 0);
    if (text[length - 1] != '\n') {
        uint32_t lines = 1;
        for (uint32_t i = 0; i != length; ++i) lines += text[i] == '\n';
        return fail(m, no_newline, lines, key_unknown, lines);
    }
    uint16_t key_at[max_keys];
    uint8_t key_length[max_keys];
    uint16_t key_line[key_count];
    for (uint32_t k = 0; k != key_count; ++k) key_line[k] = 0;
    uint32_t count = 0, line = 0, i = 0;
    while (i < length) {
        ++line;
        uint32_t end = i;
        while (text[end] != '\n') ++end;                   // the last byte is '\n'
        uint32_t a = i, b = end;
        i = end + 1;
        if (b > a && text[b - 1] == '\r') --b;
        if (b - a > max_line) return fail(m, line_too_long, line, key_unknown, line);
        for (uint32_t k = a; k != b; ++k) {
            const uint8_t c = uint8_t(text[k]);
            if (c != '\t' && (c < 0x20 || c > 0x7e)) return fail(m, bad_byte, line, key_unknown, line);
        }
        while (a < b && space(text[a])) ++a;
        if (a == b || text[a] == '#') continue;
        const uint32_t key_start = a;
        while (a < b && key_char(text[a])) ++a;
        const uint32_t n = a - key_start;
        if (!n || n > max_key) return fail(m, bad_key, line, key_unknown, line);
        const bool spaced = a < b && space(text[a]);
        while (a < b && space(text[a])) ++a;
        if (a == b || text[a] != '=')
            return fail(m, a == b || spaced ? syntax : bad_key, line, key_unknown, line);
        ++a;
        while (a < b && space(text[a])) ++a;
        while (b > a && space(text[b - 1])) --b;
        for (uint32_t k = a; k != b; ++k) if (text[k] == '\t') return fail(m, bad_byte, line, key_unknown, line);
        uint32_t key = key_unknown;
        for (uint32_t k = 0; k != key_count; ++k) if (same(text + key_start, n, key_names[k])) key = k;
        for (uint32_t k = 0; k != count; ++k)
            if (key_length[k] == n) {
                uint32_t j = 0;
                while (j != n && text[key_at[k] + j] == text[key_start + j]) ++j;
                if (j == n) return fail(m, duplicate_key, line, key, line);
            }
        if (count == max_keys) return fail(m, too_many_keys, line, key, line);
        key_at[count] = uint16_t(key_start);
        key_length[count] = uint8_t(n);
        ++count;
        const uint32_t v = b - a;
        if (key == key_unknown) {                          // ignored (010), but still bounded
            if (v > max_value) return fail(m, value_too_long, line, key, line);
            continue;
        }
        if (!v) return fail(m, empty_value, line, key, line);
        if (v > max_value) return fail(m, value_too_long, line, key, line);
        const Status s = store_value(m, key, text + a, v);
        if (s != ok) return fail(m, s, line, key, line);
        m.keys = uint16_t(m.keys | 1u << key);
        key_line[key] = uint16_t(line);
    }
    if (!count) return fail(m, empty, 0, key_unknown, line);
    if (m.type == type_companion) return fail(m, refused_type, key_line[key_type], key_type, line);
    const uint16_t allowed = m.type == type_mod_base ? base_keys : m.type == type_mod ? mod_keys : firmware_keys;
    const uint16_t extra = uint16_t(m.keys & ~(allowed | bit_type));
    if (extra) {
        uint32_t worst = key_unknown;
        for (uint32_t k = 0; k != key_count; ++k)
            if ((extra >> k & 1u) && (worst == key_unknown || key_line[k] < key_line[worst])) worst = k;
        return fail(m, key_not_for_type, key_line[worst], worst, line);
    }
    uint16_t needs = m.type == type_mod_base ? base_needs : m.type == type_mod ? mod_needs :
                     m.type == type_firmware ? firmware_needs : 0;
    if (m.type == type_none || m.type == type_firmware) {  // an assembled entry names both
        if (has(m, key_generation) || has(m, key_needs_os))
            needs = uint16_t(needs | 1u << key_generation | 1u << key_needs_os);
    }
    const uint16_t missing = uint16_t(needs & ~m.keys);
    if (missing) return fail(m, missing_key, 0, first_key(missing), line);
    m.status = ok;
    m.lines = uint16_t(line);
    return ok;
}

void legacy_info(const Manifest& m, store::Info& info) {
    zero(&info, sizeof(info));
    if (m.status != ok) return;
    for (uint32_t i = 0; i != sizeof(info.name) && i != sizeof(m.name); ++i) info.name[i] = m.name[i];
    for (uint32_t i = 0; i != sizeof(info.version) && i != sizeof(m.version); ++i) info.version[i] = m.version[i];
    info.name[sizeof(info.name) - 1] = 0;
    info.version[sizeof(info.version) - 1] = 0;
    info.kind = store::Kind(m.kind);
    info.has_bytes = has(m, key_bytes);
    info.bytes = info.has_bytes ? m.bytes : 0;
    info.has_crc32 = has(m, key_crc32);
    info.crc32 = info.has_crc32 ? m.crc32 : 0;
    info.has_generation = has(m, key_generation);
    info.has_machines = has(m, key_machines);
    info.machines = info.has_machines ? m.machines : 0;
}

namespace {
bool firmware(const Manifest& m) { return m.type == type_none || m.type == type_firmware; }
}

Dependency check_os(const Manifest& dependent, const Manifest& entry) {
    if (dependent.status != ok || entry.status != ok) return dep_invalid;
    if (!has(dependent, key_needs_os)) return dep_not_dependent;
    if (!firmware(entry)) return dep_not_firmware;
    if (!has(entry, key_sha256)) return dep_no_sha256;
    return equal32(dependent.needs_os, entry.sha256) ? dep_ok : dep_os_mismatch;
}

Dependency check_mod(const Manifest& base, const Manifest& mod) {
    if (base.status != ok || mod.status != ok) return dep_invalid;
    if (base.type != type_mod_base) return dep_not_base;
    if (mod.type != type_mod) return dep_not_mod;
    if (!equal(base.generation, mod.generation)) return dep_generation;
    if (has(mod, key_profile) && !equal(base.profile, mod.profile)) return dep_profile;
    return dep_ok;
}

Dependency check_assembled(const Manifest& base, const Manifest& assembled) {
    if (base.status != ok || assembled.status != ok) return dep_invalid;
    if (base.type != type_mod_base) return dep_not_base;
    if (!firmware(assembled)) return dep_not_firmware;
    if (!has(assembled, key_needs_os)) return dep_not_dependent;
    if (!equal(base.generation, assembled.generation)) return dep_generation;
    if (!equal32(base.needs_os, assembled.needs_os)) return dep_os_mismatch;
    if (has(assembled, key_profile) && !equal(base.profile, assembled.profile)) return dep_profile;
    return dep_ok;
}

Dependency check_apply(const Manifest& entry, const Manifest& base, const Manifest* const* mods, uint32_t count,
                       uint32_t& failed) {
    failed = count;
    if (base.status != ok || entry.status != ok) return dep_invalid;
    if (base.type != type_mod_base) return dep_not_base;
    Dependency d = check_os(base, entry);
    if (d != dep_ok) return d;
    for (uint32_t i = 0; i != count; ++i) {
        d = mods && mods[i] ? check_mod(base, *mods[i]) : dep_invalid;
        if (d != dep_ok) { failed = i; return d; }
    }
    return dep_ok;
}

bool needs(const Manifest& dependent, const Manifest& entry) { return check_os(dependent, entry) == dep_ok; }
} }
