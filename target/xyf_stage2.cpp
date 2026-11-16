#include "xyf_stage2.hpp"
#include "xyf_crc32.hpp"
#include "../native/hash.hpp"
namespace xyf { namespace stage2 {
namespace {
uint32_t get32(const uint8_t* p) { return p[0] | uint32_t(p[1]) << 8 | uint32_t(p[2]) << 16 | uint32_t(p[3]) << 24; }
}
Check validate(const uint8_t* file, uint32_t length, uint32_t stage1_version, Header& out) {
    uint8_t* h = reinterpret_cast<uint8_t*>(&out);
    for (uint32_t i = 0; i != sizeof(out); ++i) h[i] = 0;
    if (!file || length < header_bytes) return Check::size;
    for (uint32_t i = 0; i != header_bytes; ++i) h[i] = file[i];      // byte copy: no alignment assumptions
    if (out.magic != magic) return Check::magic;
    if (out.abi != abi || out.header_bytes != header_bytes) return Check::abi;
    if (crc::update(0, file, header_bytes - 4) != get32(file + header_bytes - 4)) return Check::header_crc;
    if (out.flags || out.reserved) return Check::flags;
    if (!out.image_bytes || out.image_bytes > max_image || length != header_bytes + out.image_bytes) return Check::size;
    if (out.load_address < window_begin || out.load_address & 3u ||
        out.load_address > window_end - out.image_bytes) return Check::window;
    if (out.entry < out.load_address || out.entry >= out.load_address + out.image_bytes || (out.entry & 1u))
        return Check::entry;
    if (crc::update(0, file + header_bytes, out.image_bytes) != out.image_crc32) return Check::image_crc;
    if (out.min_stage1 > stage1_version) return Check::stage1;
    bool terminated = false;
    for (uint32_t i = 0; i != sizeof(out.build_id); ++i) if (!out.build_id[i]) { terminated = true; break; }
    if (!terminated) return Check::build_id;
    return Check::ok;
}
Check accept(const uint8_t* file, uint32_t length, uint32_t stage1_version, Header& out) {
    const Check check = validate(file, length, stage1_version, out);
    if (check != Check::ok) return check;
    xyf_core::hash::Sha256 hash;
    xyf_core::hash::Digest digest;
    if (!hash.update(file + header_bytes, out.image_bytes) || !hash.finish(digest)) return Check::image_sha;
    uint8_t differ = 0;
    for (uint32_t i = 0; i != 32; ++i) differ |= uint8_t(digest.bytes[i] ^ out.image_sha256[i]);
    return differ ? Check::image_sha : Check::ok;
}
Blob classify(const uint8_t* data, uint32_t size, uint32_t stage1_version, Header& out, uint32_t& detail) {
    detail = 0;
    if (data && size == 8 && get32(data) == refusal_magic) {
        detail = get32(data + 4);
        uint8_t* h = reinterpret_cast<uint8_t*>(&out);
        for (uint32_t i = 0; i != sizeof(out); ++i) h[i] = 0;
        return Blob::refused;
    }
    const Check check = accept(data, size, stage1_version, out);
    detail = uint32_t(check);
    return check == Check::ok ? Blob::ready : Blob::invalid;
}
Next after(uint32_t exit, bool link_lost) {
    if (link_lost) return Next::boot_builtin;
    return exit == exit_menu ? Next::menu : Next::boot_default;   // unknown codes: as exit_continue
}
#if !defined(__bfin__) || defined(XYF_STAGE2_MENU)
Do decide(uint32_t exit, bool link_lost, uint32_t catalog_count, uint32_t features, uint32_t& entry) {
    entry = 0;
    if (link_lost) return Do::boot_builtin;
    const uint32_t code = exit & 0xffu, argument = exit >> 8;
    switch (code) {
    case request_menu: return Do::menu;
    case request_boot_once:
        if (!(features & feature_catalog) || argument >= catalog_count) return Do::note_no_entry;
        entry = argument;
        return Do::boot_entry;
    case request_set_default:
        if (!(features & feature_set_default)) return Do::note_unavailable;
        if (!(features & feature_catalog) || argument >= catalog_count) return Do::note_no_entry;
        entry = argument;
        return Do::set_default;
    case request_mtp: return features & feature_mtp ? Do::mtp : Do::note_unavailable;
    case request_uninstall: return Do::note_unavailable;   // no uninstall in any stage-1 build yet
    default: return Do::boot_default;       // request_continue and unknown codes (as ABI 1)
    }
}
#endif
} }
