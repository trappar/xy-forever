#ifndef XYF_MODS_STORE_HPP
#define XYF_MODS_STORE_HPP
#include <stdint.h>
// The device's mod package store (#124, docs/storage/mods.md): mods/<generation>/store.txt names its
// version, "version=<n>" on the first line. Version 1: every package file right in its package folder,
// a file with a folder in its path under native/mod_job.hpp's stored_name. No store.txt: an older layout
// (mods/<profile>/<generation>/, or the tree of protocol 1.16's first builds) this reader does not take.
namespace xyf { namespace mods_store {
static const uint32_t version = 1;
static const char file[] = "store.txt";
// The version store.txt's first bytes name (at most 9 digits), 0 when they name none.
inline __attribute__((always_inline)) uint32_t version_of(const uint8_t* text, uint32_t bytes) {
    const char* key = "version=";
    uint32_t i = 0, n = 0, digits = 0;
    for (; key[i]; ++i) if (i >= bytes || text[i] != uint8_t(key[i])) return 0;
    for (; i < bytes && text[i] >= '0' && text[i] <= '9' && digits < 9; ++i, ++digits) n = n * 10 + (text[i] - '0');
    const bool ends = i == bytes || text[i] == '\n' || text[i] == '\r';
    return digits && ends ? n : 0;
}
} }
#endif
