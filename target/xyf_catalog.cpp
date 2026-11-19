#include "xyf_catalog.hpp"
namespace xyf { namespace catalog {
namespace {
void put32(uint8_t* p, uint32_t v) { p[0] = uint8_t(v); p[1] = uint8_t(v >> 8); p[2] = uint8_t(v >> 16); p[3] = uint8_t(v >> 24); }
uint32_t get32(const uint8_t* p) { return p[0] | uint32_t(p[1]) << 8 | uint32_t(p[2]) << 16 | uint32_t(p[3]) << 24; }
uint32_t crc32(const uint8_t* data, uint32_t count) {
    uint32_t crc = 0xffffffffu;
    for (uint32_t i = 0; i != count; ++i) {
        crc ^= data[i];
        for (uint32_t b = 0; b != 8; ++b) crc = (crc >> 1) ^ (0xedb88320u & (0u - (crc & 1u)));
    }
    return ~crc;
}
void field(uint8_t* out, uint32_t size, const char* text) {
    uint32_t i = 0;
    for (; text[i] && i + 1 < size; ++i) out[i] = uint8_t(text[i]);
    for (; i != size; ++i) out[i] = 0;
}
bool terminated(const char* s, uint32_t size) {
    for (uint32_t i = 0; i != size; ++i) if (!s[i]) return true;
    return false;
}
int compare(const char* a, const char* b) {
    while (*a && *a == *b) ++a, ++b;
    return int(static_cast<unsigned char>(*a)) - int(static_cast<unsigned char>(*b));
}
}

uint32_t encode(const library::Library& library, uint8_t* out, uint32_t capacity, uint32_t storage) {
    const uint32_t count = library.count > max_entries ? max_entries : library.count;
    const uint32_t bytes = header_bytes + count * sizeof(Item) + 4;
    if (capacity < bytes) return 0;
    uint32_t default_index = 0xffffffffu;
    for (uint32_t i = 0; i != count; ++i)
        if (library.state.default_id[0] && !compare(library.entries[i].id, library.state.default_id)) default_index = i;
    put32(out, magic); put32(out + 4, count); put32(out + 8, default_index); put32(out + 12, storage);
    for (uint32_t i = 0; i != count; ++i) {
        const library::Entry& e = library.entries[i];
        uint8_t* r = out + header_bytes + i * sizeof(Item);
        field(r, 32, e.id);
        field(r + 32, 48, e.info.name);
        field(r + 80, 32, e.info.version);
        r[112] = uint8_t(e.info.kind);
        r[113] = uint8_t((e.usable ? 1 : 0) | (e.image_present && e.content_missing && !e.invalid_record ? flag_content_missing : 0) |
                         (e.invalid_record ? flag_invalid_record : 0));
        const uint32_t machines = e.machines > 0xffffu ? 0xffffu : e.machines;
        r[114] = uint8_t(machines); r[115] = uint8_t(machines >> 8);
    }
    put32(out + bytes - 4, crc32(out, bytes - 4));
    return bytes;
}

bool decode(const uint8_t* data, uint32_t length, Catalog& out) {
    for (uint32_t i = 0; i != sizeof(out); ++i) reinterpret_cast<char*>(&out)[i] = 0;
    out.default_index = 0xffffffffu;
    if (!data || length < header_bytes + 4 || (get32(data) != magic && get32(data) != legacy_magic)) return false;
    const uint32_t allowed_flags = get32(data) == legacy_magic ? 15u : 31u;
    const uint32_t count = get32(data + 4), default_index = get32(data + 8);
    if (count > max_entries || length != header_bytes + count * sizeof(Item) + 4) return false;
    if (default_index != 0xffffffffu && default_index >= count) return false;
    if (get32(data + length - 4) != crc32(data, length - 4)) return false;
    for (uint32_t i = 0; i != count; ++i) {
        const uint8_t* r = data + header_bytes + i * sizeof(Item);
        Item& item = out.items[i];
        for (uint32_t k = 0; k != 112; ++k) reinterpret_cast<uint8_t*>(item.id)[k] = r[k];   // id, name, version
        item.kind = r[112]; item.flags = r[113]; item.machines = uint16_t(r[114] | r[115] << 8);
        if (!terminated(item.id, 32) || !terminated(item.name, 48) || !terminated(item.version, 32) ||
            !store::valid_id(item.id) || item.kind > store::kind_custom || (item.flags & ~allowed_flags)) return false;
    }
    out.count = count;
    out.default_index = default_index;
    return true;
}

void describe(const Item& item, char* out, uint32_t capacity) {
    if (!capacity) return;
    const char* kind = item.kind == store::kind_official ? "official" : item.kind == store::kind_open_xy ? "open-xy" :
                       item.kind == store::kind_custom ? "custom" : "";
    uint32_t at = 0;
    auto put = [&](const char* s) { for (; *s && at + 1 < capacity; ++s) out[at++] = *s; };
    if (item.flags & flag_invalid_record) put("invalid manifest.json");
    else if (item.flags & flag_content_missing) put("content not installed");
    else if (!(item.flags & 1)) put("missing os.ldr");
    else {
        put(kind);
        if (item.machines) {
            char digits[6]; uint32_t v = item.machines, n = 0;
            static const uint32_t powers[5] = {10000, 1000, 100, 10, 1};
            for (uint32_t p = 0; p != 5; ++p) { char d = '0'; while (v >= powers[p]) { v -= powers[p]; ++d; } if (n || d != '0' || p == 4) digits[n++] = d; }
            digits[n] = 0;
            if (*kind) put(", ");
            put(digits);
            put(item.machines == 1 ? " machine" : " machines");
        }
    }
    out[at] = 0;
}
} }
