#include "../native/hash.hpp"
extern "C" int sha256_digest(const unsigned char* data, unsigned int size,
                               unsigned int chunk, unsigned char* output) {
    if (!chunk || !output) return 1;
    xyf_core::hash::Sha256 state;
    unsigned int at = 0;
    while (at < size) {
        const unsigned int amount = size - at < chunk ? size - at : chunk;
        if (!state.update(data + at, amount)) return 2;
        at += amount;
    }
    xyf_core::hash::Digest digest;
    if (!state.finish(digest) || state.update(0, 0) || state.finish(digest)) return 3;
    for (unsigned int i = 0; i < 32; ++i) output[i] = digest.bytes[i];
    return 0;
}
