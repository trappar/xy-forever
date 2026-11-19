#ifndef XYF_BLOB_GC_HPP
#define XYF_BLOB_GC_HPP
#include "xyf_blob.hpp"
#include "xyf_manifest.hpp"
namespace xyf { namespace blob {
// One complete reachability snapshot, using 4 MiB of the completed job's reusable
// workspace (not its status/slot or MTP's reserved slot). Each kept list is streamed
// once. Invalid/newer/oversized lists stop before any deletion. Sort the raw hashes
// in bounded heap steps, then binary-search each candidate blob's name.
static const uint32_t index_bytes = 0x400000u;
struct Collect {
    manifest::ListReader reader;
    manifest::HashIndex refs;
    char candidate[65], entry[240], path[240];
    uint8_t expected[32], buffer[1024];
    uint32_t stage, blob_skip, entry_skip, which, removed, cursor, sorting;
    int fd;
};
static inline void collect_begin(Collect& g, uint8_t* workspace, uint32_t bytes) {
    g.stage = g.blob_skip = g.entry_skip = g.which = g.removed = g.cursor = g.sorting = 0;
    g.fd = -1; g.refs.data = workspace; g.refs.count = 0; g.refs.capacity = bytes >> 5;
}
static inline int compare(const uint8_t* a, const uint8_t* b) {
    for (uint32_t i = 0; i != 32; ++i) if (a[i] != b[i]) return a[i] < b[i] ? -1 : 1;
    return 0;
}
static inline void exchange(Collect& g, uint32_t a, uint32_t b) {
    for (uint32_t i = 0; i != 32; ++i) {
        const uint8_t t = g.refs.data[a * 32u + i];
        g.refs.data[a * 32u + i] = g.refs.data[b * 32u + i]; g.refs.data[b * 32u + i] = t;
    }
}
static inline void sift(Collect& g, uint32_t at, uint32_t count) {
    for (;;) {
        uint32_t child = at * 2u + 1;
        if (child >= count) return;
        if (child + 1 < count && compare(g.refs.data + child * 32u, g.refs.data + (child + 1) * 32u) < 0) ++child;
        if (compare(g.refs.data + at * 32u, g.refs.data + child * 32u) >= 0) return;
        exchange(g, at, child); at = child;
    }
}
static inline bool contains(Collect& g) {
    uint32_t low = 0, high = g.refs.count;
    while (low < high) {
        const uint32_t middle = low + ((high - low) >> 1);
        const int order = compare(g.refs.data + middle * 32u, g.expected);
        if (!order) return true;
        if (order < 0) low = middle + 1; else high = middle;
    }
    return false;
}
// 0 more, 1 complete, -1 failed. One bounded IO or heap operation per step.
template<class Io> int collect_step(Collect& g, Io& io) {
    uint32_t kind = 0;
    char name[256];
    int n;
    switch (g.stage) {
    case 0:
        n = io.list_one("/yaffs2/xy-forever/firmware", g.entry_skip, name, kind);
        if (n < 0) return -1;
        if (!n) { g.cursor = g.refs.count >> 1; g.stage = 5; return 0; }
        ++g.entry_skip;
        if (kind != 1) return 0;
        if (!path(g.entry, "/yaffs2/xy-forever/firmware/", name)) return -1;
        g.which = 0; g.stage = 1; return 0;
    case 1:
        if (!path(g.path, g.entry, g.which ? "/files.sha256" : "/content.sha256")) return -1;
        g.fd = io.open(g.path, false);
        if (g.fd == -2) { g.stage = 4; return 0; }
        if (g.fd < 0) return -1;
        manifest::list_begin(g.reader); g.stage = 2; return 0;
    case 2:
        n = io.read(g.fd, g.buffer, sizeof(g.buffer));
        if (n < 0 || n > int(sizeof(g.buffer))) return -1;
        if (n) return manifest::list_index_feed(g.reader, reinterpret_cast<const char*>(g.buffer), uint32_t(n),
                                                g.refs, g.which != 0) == manifest::list_ok ? 0 : -1;
        if (manifest::list_end(g.reader) != manifest::list_ok) return -1;
        g.stage = 3; return 0;
    case 3:
        n = io.close(g.fd); g.fd = -1;
        if (n) return -1;
        g.stage = 4; return 0;
    case 4:
        if (!g.which) { g.which = 1; g.stage = 1; } else g.stage = 0;
        return 0;
    case 5:
        if (g.cursor) { sift(g, --g.cursor, g.refs.count); return 0; }
        g.sorting = g.refs.count; g.stage = 6; return 0;
    case 6:
        if (g.sorting > 1) { exchange(g, 0, --g.sorting); sift(g, 0, g.sorting); return 0; }
        g.stage = 7; return 0;
    case 7:
        n = io.list_one(root, g.blob_skip, name, kind);
        if (n < 0) return -1;
        if (!n) return 1;
        ++g.blob_skip;
        if (kind != 0) return 0;
        for (uint32_t i = 0; i != 64; ++i) {
            const char c = name[i];
            if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'))) return 0;
            g.candidate[i] = c;
            const uint8_t v = uint8_t(c <= '9' ? c - '0' : c - 'a' + 10);
            if (i & 1u) g.expected[i >> 1] |= v; else g.expected[i >> 1] = uint8_t(v << 4);
        }
        if (name[64]) return 0;
        g.candidate[64] = 0;
        if (!contains(g)) g.stage = 8;
        return 0;
    default:
        if (!path(g.path, root, "/")) return -1;
        { char target[240]; if (!path(target, g.path, g.candidate) || io.unlink(target) < 0) return -1; }
        --g.blob_skip; ++g.removed; g.stage = 7; return 0;
    }
}
} }
#endif
