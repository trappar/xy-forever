#ifndef XYF_MANAGED_BLOB_LINK_HPP
#define XYF_MANAGED_BLOB_LINK_HPP
#include "file_engine.hpp"

namespace xy_forever { namespace managed_blob_link {
// create, put (#195): B writes a sealed file itself. create opens a new file (mode 0444) in XY Forever's scratch on A;
// each put (a write-class request whose descriptor is put_handle) writes B's piece to it; copy then closes it. B
// hashes what it sends: no copy and no read-back.
enum Operation : uint32_t { stage = 0, seal = 1, copy = 2, create = 3, put = 4 };
constexpr uint32_t piece_bytes = 4096;
constexpr uint32_t version = 1, tag = 0x004d4200u, mask = 0x00ffff00u;
constexpr char scratch[] = "/yaffs2/xy-forever/tmp/";
constexpr char tail[] = "-b10b1a5e", seal_tail[] = "-b10b5ea1";
constexpr char blob_dir[] = "/yaffs2/xy-forever/blobs/";
constexpr uint32_t put_handle = version << 24 | tag | put;
static_assert(sizeof(tail) == sizeof(seal_tail), "same encoding for both scratch names");
constexpr uint32_t name_bytes = 4 + 8 + sizeof(tail) - 1;
constexpr uint32_t scratch_bytes = sizeof(scratch) - 1 + name_bytes;
constexpr uint32_t frame_bytes = scratch_bytes + 1 + 64 + 1;
static_assert(frame_bytes <= file_engine::max_path, "managed link fits the stock path body");

// Authenticated 1.2.1 dispatch tables for the hardlink creator, allocation, object headers, removal and their
// shared flash path. A checks these read-only data spans before any new stock call. They are outside the
// ordinary engine CRCs.
constexpr uint32_t creator_spans[][3] = {
    {0x11800a84u, 0x18u, 0xad982213u}, {0x1180106cu, 0x18u, 0xe68987f3u}, {0x118011b8u, 0x18u, 0xe2084b04u},
    {0x1180131cu, 0x18u, 0x6775f8d6u}, {0x11801228u, 0x18u, 0x4ec5629au}, {0x11801d5cu, 0x18u, 0xcc8ddc22u},
};

inline bool hex(char c) { return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'); }
inline void temporary(char path[file_engine::max_path], uint32_t bytes, bool sealed = false) {
    uint32_t at = 0;
    for (char c : scratch)
        if (c) path[at++] = c;
    for (char c : "put-")
        if (c) path[at++] = c;
    for (uint32_t i = 0; i != 8; ++i) path[at++] = "0123456789abcdef"[(bytes >> (28 - 4 * i)) & 15u];
    const char* end = sealed ? seal_tail : tail;
    for (uint32_t i = 0; i != sizeof(tail); ++i) path[at++] = end[i];
}
inline bool temporary_path(const char* path, bool sealed = false) {
    for (uint32_t i = 0; i != sizeof(scratch) - 1; ++i)
        if (*path++ != scratch[i]) return false;
    for (const char* p = "put-"; *p; ++p)
        if (*path++ != *p) return false;
    for (uint32_t i = 0; i != 8; ++i)
        if (!hex(*path++)) return false;
    const char* end = sealed ? seal_tail : tail;
    for (uint32_t i = 0; i != sizeof(tail); ++i)
        if (path[i] != end[i]) return false;
    return true;
}
// create's path: a new file somewhere below the scratch folder.
inline bool scratch_path(const char* path) {
    for (uint32_t i = 0; i != sizeof(scratch) - 1; ++i)
        if (*path++ != scratch[i]) return false;
    return *path != 0;
}
inline bool decode(char* path, const char*& hash, bool sealed = false) {
    for (uint32_t i = 0; i != frame_bytes - 1; ++i)
        if (!path[i]) return false;
    if (path[scratch_bytes] != file_engine::upload_mark || path[frame_bytes - 1]) return false;
    path[scratch_bytes] = 0;
    if (!temporary_path(path, sealed)) return false;
    hash = path + scratch_bytes + 1;
    for (uint32_t i = 0; i != 64; ++i)
        if (!hex(hash[i])) return false;
    return true;
}
inline bool encode(const char* path, const uint8_t* sha, char* frame, bool sealed = false) {
    if (!temporary_path(path, sealed)) return false;
    for (uint32_t i = 0; i != scratch_bytes; ++i) frame[i] = path[i];
    frame[scratch_bytes] = file_engine::upload_mark;
    for (uint32_t i = 0; i != 64; ++i) {
        if (!hex(char(sha[i]))) return false;
        frame[scratch_bytes + 1 + i] = char(sha[i]);
    }
    frame[frame_bytes - 1] = 0;
    return true;
}
inline void blob_path(char* path, const char* hash) {
    uint32_t n = 0;
    for (char c : blob_dir)
        if (c) path[n++] = c;
    for (uint32_t i = 0; i != 64; ++i) path[n++] = hash[i];
    path[n] = 0;
}
inline bool valid_blob_path(const char* path) {
    for (uint32_t i = 0; i != sizeof(blob_dir) - 1; ++i)
        if (*path++ != blob_dir[i]) return false;
    for (uint32_t i = 0; i != 64; ++i)
        if (!hex(*path++)) return false;
    return !*path;
}
}}
#endif
