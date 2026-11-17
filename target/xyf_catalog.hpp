#ifndef XYF_CATALOG_HPP
#define XYF_CATALOG_HPP
#include <stdint.h>
#include "xyf_library.hpp"
// Compact library catalog: processor a encodes its library scan, the link
// carries the bytes (image-transfer path), processor b decodes them for the
// menu. Fixed-size little-endian records with a CRC-32 trailer; decode()
// rejects anything malformed (magic, counts, unterminated strings, CRC).
namespace xyf { namespace catalog {
static const uint32_t magic = 0x31435958u;          // "XYC1"
static const uint32_t max_entries = library::max_entries;
static const uint8_t flag_usable = 1, flag_content_missing = 8;
struct Item {
    char id[32], name[48], version[32];
    uint8_t kind, flags;                             // flags: 1 usable, 2 has_layout, 4 layout_ok,
                                                     // 8 content not installed (then not usable)
    uint16_t machines;
};
static_assert(sizeof(Item) == 116, "Item layout");
struct Catalog {
    uint32_t count, default_index;                   // default_index = 0xffffffff when none
    Item items[max_entries];
};
static const uint32_t header_bytes = 16;             // magic, count, default_index, reserved
static const uint32_t max_bytes = header_bytes + max_entries * sizeof(Item) + 4;
// Returns bytes written (0 if capacity is too small).
// storage: the header's reserved word (191: A's storage start, xyf_boot.hpp; 0 before).
uint32_t encode(const library::Library& library, uint8_t* out, uint32_t capacity, uint32_t storage = 0);
bool decode(const uint8_t* data, uint32_t length, Catalog& out);
// The header's reserved word of a catalog decode() accepted (191: A's storage start).
inline uint32_t reserved(const uint8_t* data) {
    return data[12] | uint32_t(data[13]) << 8 | uint32_t(data[14]) << 16 | uint32_t(data[15]) << 24;
}
// Detail text for a menu row ("official", "open-xy, 6 machines", ...), at most 23 chars.
void describe(const Item& item, char* out, uint32_t capacity);
} }
#endif
