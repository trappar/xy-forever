#ifndef XYF_BLOB_HPP
#define XYF_BLOB_HPP
#include <stdint.h>
#include "../native/hash.hpp"
// Raw content-addressed files: their name is the SHA-256 of their complete bytes.
// One bounded IO per step; publication only after the source and read-back hashes agree.
namespace xyf { namespace blob {
static const char root[] = "/yaffs2/xy-forever/blobs";
static const char pack_refs[] = "xyf-pack-v1.sha256";
struct Copy {
    char source[240], temporary[240], target[240];
    uint8_t expected[32], buffer[1024];
    xyf_core::hash::Sha256 hash;
    uint32_t stage, count, written;
    int input, output;
};
static inline bool path(char* out, const char* a, const char* b) {
    uint32_t n = 0;
    for (; *a; ++a) {
        if (n == 239) return false;
        out[n++] = *a;
    }
    for (; *b; ++b) {
        if (n == 239) return false;
        out[n++] = *b;
    }
    out[n] = 0;
    return true;
}
static inline void scratch_name(char* out, uint32_t size) {
    static const char prefix[] = "/yaffs2/xy-forever/tmp/put-";
    uint32_t n = 0;
    for (; prefix[n]; ++n) out[n] = prefix[n];
    for (uint32_t i = 0; i != 8; ++i) out[n + i] = "0123456789abcdef"[(size >> (28 - 4 * i)) & 15u];
    static const char tail[] = "-b10bcace";
    for (uint32_t i = 0; i != sizeof(tail); ++i) out[n + 8 + i] = tail[i];
}
static inline bool begin(Copy& c, const char* source, const char* temporary, const uint8_t* expected) {
    c.stage = 0;
    c.input = c.output = -1;
    for (uint32_t i = 0; i != 32; ++i) c.expected[i] = expected[i];
    char name[66];
    name[0] = '/';
    for (uint32_t i = 0; i != 32; ++i) {
        name[1 + 2 * i] = "0123456789abcdef"[expected[i] >> 4];
        name[2 + 2 * i] = "0123456789abcdef"[expected[i] & 15];
    }
    name[65] = 0;
    return path(c.source, source, "") && path(c.temporary, temporary, "") && path(c.target, root, name);
}
static inline bool matches(Copy& c) {
    xyf_core::hash::Digest d;
    if (!c.hash.finish(d)) return false;
    uint32_t diff = 0;
    for (uint32_t i = 0; i != 32; ++i) diff |= d.bytes[i] ^ c.expected[i];
    return !diff;
}
// 0 more, 1 complete, -1 failed. A failed/cut copy leaves only scratch bytes.
template<class Io> int step(Copy& c, Io& io) {
    int n;
    switch (c.stage) {
    case 0:
        c.input = io.open(c.target, false);
        if (c.input == -2) {
            c.stage = 3;
            return 0;
        }
        if (c.input < 0) return -1;
        c.hash.reset();
        c.stage = 1;
        return 0;
    case 1:
        n = io.read(c.input, c.buffer, sizeof(c.buffer));
        if (n < 0 || n > int(sizeof(c.buffer))) return -1;
        if (n) {
            c.hash.update(c.buffer, uint32_t(n));
            return 0;
        }
        c.stage = matches(c) ? 2u : 15u;
        return 0;
    case 2:
        n = io.close(c.input);
        c.input = -1;
        return n ? -1 : 1;
    case 3:
        if (io.mkdir(root) < 0) return -1;
        c.stage = 4;
        return 0;
    case 4:
        c.input = io.open(c.source, false);
        if (c.input < 0) return -1;
        c.hash.reset();
        c.stage = 5;
        return 0;
    case 5:
        c.output = io.open(c.temporary, true);
        if (c.output < 0) return -1;
        c.stage = 6;
        return 0;
    case 6:
        n = io.read(c.input, c.buffer, sizeof(c.buffer));
        if (n < 0 || n > int(sizeof(c.buffer))) return -1;
        if (!n) {
            c.stage = matches(c) ? 8u : 15u;
            return 0;
        }
        c.hash.update(c.buffer, uint32_t(n));
        c.count = uint32_t(n);
        c.written = 0;
        c.stage = 7;
        return 0;
    case 7:
        n = io.write(c.output, c.buffer + c.written, c.count - c.written);
        if (n <= 0 || uint32_t(n) > c.count - c.written) return -1;
        c.written += uint32_t(n);
        if (c.written == c.count) c.stage = 6;
        return 0;
    case 8:
        n = io.close(c.input);
        c.input = -1;
        if (n) return -1;
        c.stage = 9;
        return 0;
    case 9:
        n = io.close(c.output);
        c.output = -1;
        if (n) return -1;
        c.stage = 10;
        return 0;
    case 10:
        c.input = io.open(c.temporary, false);
        if (c.input < 0) return -1;
        c.hash.reset();
        c.stage = 11;
        return 0;
    case 11:
        n = io.read(c.input, c.buffer, sizeof(c.buffer));
        if (n < 0 || n > int(sizeof(c.buffer))) return -1;
        if (n) {
            c.hash.update(c.buffer, uint32_t(n));
            return 0;
        }
        c.stage = matches(c) ? 12u : 15u;
        return 0;
    case 12:
        n = io.close(c.input);
        c.input = -1;
        if (n) return -1;
        c.stage = 13;
        return 0;
    case 13: return io.rename(c.temporary, c.target) ? -1 : 1;
    default: return -1;
    }
}
template<class Io> void abandon(Copy& c, Io& io) {
    if (c.input >= 0) io.close(c.input);
    if (c.output >= 0) io.close(c.output);
    c.input = c.output = -1;
}
}}
#endif
