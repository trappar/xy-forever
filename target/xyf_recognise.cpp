#include "xyf_manifest.hpp"
// The recogniser (xyf_manifest.hpp): magic bytes for files, shape and info.txt type for folders.
// The magics are the ones native/tfw.cpp (wrapper, TESG), native/ldr.cpp (first block) and
// xyf_stage2.hpp check; this reads at most prefix_bytes and decodes nothing.
namespace xyf { namespace manifest {
namespace {
char lower(char c) { return c >= 'A' && c <= 'Z' ? char(c + 'a' - 'A') : c; }
uint32_t length(const char* s) { uint32_t n = 0; while (s[n]) ++n; return n; }
bool same(const char* a, const char* b) {             // ASCII case ignored
    while (*a && lower(*a) == lower(*b)) ++a, ++b;
    return lower(*a) == lower(*b);
}
bool exact(const char* a, const char* b) {            // the shape names: case matters (yaffs)
    while (*a && *a == *b) ++a, ++b;
    return *a == *b;
}
bool ends(const char* s, const char* tail) {
    const uint32_t n = length(s), t = length(tail);
    return n >= t && same(s + n - t, tail);
}
bool bootloader(const char* s) {
    static const char word[] = "bootloader";
    for (uint32_t i = 0; s[i]; ++i) {
        uint32_t j = 0;
        while (j != 10 && s[i + j] && lower(s[i + j]) == word[j]) ++j;
        if (j == 10) return true;
    }
    return false;
}
// The companion images by the names the official packages give them (package_policy rules).
bool companion(const char* s) { return ends(s, ".cyacd") || same(s, "ble.bin"); }
uint32_t word(const uint8_t* p) { return uint32_t(p[0]) | uint32_t(p[1]) << 8 | uint32_t(p[2]) << 16 | uint32_t(p[3]) << 24; }
uint32_t half(const uint8_t* p) { return uint32_t(p[0]) | uint32_t(p[1]) << 8; }
bool magic(const uint8_t* p, uint8_t a, uint8_t b, uint8_t c, uint8_t d) {
    return p[0] == a && p[1] == b && p[2] == c && p[3] == d;
}
bool accepted(uint8_t kind) {
    return kind == kind_firmware_entry || kind == kind_assembled_entry || kind == kind_official_tfw ||
           kind == kind_ldr_image || kind == kind_xyf_stage2 || kind == kind_mod_base || kind == kind_mod ||
           kind == kind_content_pack;
}
void set(Recognition& out, uint8_t kind, uint8_t reason, bool content_set = false) {
    out.kind = kind;
    out.reason = reason;
    out.accepted = reason == reason_ok && accepted(kind);
    out.content_set = content_set;
}
bool refused_name(const char* name, Recognition& out) {
    if (bootloader(name)) { set(out, kind_bootloader, reason_refused); return true; }
    if (companion(name)) { set(out, kind_companion, reason_refused); return true; }
    return false;
}
}

bool valid_name(const char* name) {
    if (!name) return false;
    uint32_t n = 0;
    for (; name[n]; ++n) {
        const uint8_t c = uint8_t(name[n]);
        if (n == 127 || c < 0x20 || c > 0x7e || c == '/' || c == '\\' || c == ':') return false;
    }
    return n && name[n - 1] != '.' && name[n - 1] != ' ';   // also refuses "." and ".."
}

void recognise_file(const char* name, uint32_t size, const uint8_t* p, uint32_t n, Recognition& out) {
    if (!valid_name(name)) return set(out, kind_unknown, reason_bad_name);
    if (refused_name(name, out)) return;
    if (!p) n = 0;
    if (n > size) n = size;
    if (n < 4) return set(out, kind_unknown, size > n ? reason_short : reason_unknown);
    // .tfw: babecafe + beefcafe at 0x40 (crypto header at 0x80), or a bare beefcafe (at 0x40);
    // then the crypto header's TESG and its two format words (native/tfw.cpp parse).
    uint32_t base = 0;
    if (magic(p, 0xba, 0xbe, 0xca, 0xfe)) {
        if (n < 0x44) return set(out, kind_unknown, reason_short);
        if (!magic(p + 0x40, 0xbe, 0xef, 0xca, 0xfe)) return set(out, kind_unknown, reason_unknown);
        base = 0x80;
    } else if (magic(p, 0xbe, 0xef, 0xca, 0xfe)) {
        base = 0x40;
    }
    if (base) {
        if (n < base + 0x8c) return set(out, kind_unknown, reason_short);
        const bool tesg = magic(p + base + 0x84, 'T', 'E', 'S', 'G') && half(p + base + 0x88) == 1 &&
                          half(p + base + 0x8a) == 1 && size >= base + 0x300;
        return set(out, tesg ? kind_official_tfw : kind_unknown, tesg ? reason_ok : reason_unknown);
    }
    if (magic(p, 'X', 'Y', 'S', '2'))                         // xyf::stage2::magic, 96-byte header
        return set(out, size >= 96 ? kind_xyf_stage2 : kind_unknown, size >= 96 ? reason_ok : reason_unknown);
    if (p[3] == 0xad) {                                       // LDR: the first block (native/ldr.cpp)
        if (n < 16) return set(out, kind_unknown, reason_short);
        uint8_t sum = 0;
        for (uint32_t i = 0; i != 16; ++i) sum = uint8_t(sum ^ p[i]);
        const bool ldr = !sum && half(p) == 0x5004 && !word(p + 8) && size >= 32 && size <= 16u * 1024u * 1024u &&
                         word(p + 12) == size - 16;
        return set(out, ldr ? kind_ldr_image : kind_unknown, ldr ? reason_ok : reason_unknown);
    }
    if (magic(p, 'P', 'K', 3, 4) || magic(p, 'P', 'K', 5, 6)) return set(out, kind_zip, reason_host_only);
    set(out, kind_unknown, reason_unknown);
}

void recognise_folder(const char* name, const char* const* names, uint32_t count, const Manifest* info,
                      Recognition& out) {
    if (!valid_name(name) || (count && !names)) return set(out, kind_unknown, reason_bad_name);
    bool has_info = false, has_ldr = false, has_pack = false, has_list = false,
         has_version = false;
    for (uint32_t i = 0; i != count; ++i) {
        if (!valid_name(names[i])) return set(out, kind_unknown, reason_bad_name);
        if (refused_name(names[i], out)) return;
        has_info |= exact(names[i], "info.txt");
        has_ldr |= exact(names[i], "os.ldr");
        has_pack |= exact(names[i], "manifest.json");
        has_list |= exact(names[i], "content.sha256");
        has_version |= exact(names[i], "firmware.version");
    }
    const bool content_set = has_list && has_version;
    if (has_info) {
        if (!info) return set(out, kind_unknown, reason_need_manifest);
        if (info->status == refused_type) return set(out, kind_companion, reason_refused);
        if (info->status != ok) return set(out, kind_unknown, reason_manifest);
        if (info->type == type_mod_base) return set(out, kind_mod_base, reason_ok);
        if (info->type == type_mod) return set(out, kind_mod, reason_ok);
        const uint8_t kind = has(*info, key_generation) ? kind_assembled_entry : kind_firmware_entry;
        if (!has_ldr) return set(out, kind, reason_missing_image);
        if (has_list != has_version) return set(out, kind, reason_content_set);
        return set(out, kind, reason_ok, content_set);
    }
    if (has_pack) return set(out, has_ldr ? kind_unknown : kind_content_pack, has_ldr ? reason_ambiguous : reason_ok);
    if (has_ldr) {
        if (has_list != has_version) return set(out, kind_firmware_entry, reason_content_set);
        return set(out, kind_firmware_entry, reason_ok, content_set);
    }
    set(out, kind_unknown, reason_unknown);
}
} }
