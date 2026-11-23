#include "hash.hpp"
namespace xyf_core { namespace hash {
namespace {
const U32 constants[64] = {
    0x428a2f98u, 0x71374491u, 0xb5c0fbcfu, 0xe9b5dba5u, 0x3956c25bu, 0x59f111f1u, 0x923f82a4u, 0xab1c5ed5u,
    0xd807aa98u, 0x12835b01u, 0x243185beu, 0x550c7dc3u, 0x72be5d74u, 0x80deb1feu, 0x9bdc06a7u, 0xc19bf174u,
    0xe49b69c1u, 0xefbe4786u, 0x0fc19dc6u, 0x240ca1ccu, 0x2de92c6fu, 0x4a7484aau, 0x5cb0a9dcu, 0x76f988dau,
    0x983e5152u, 0xa831c66du, 0xb00327c8u, 0xbf597fc7u, 0xc6e00bf3u, 0xd5a79147u, 0x06ca6351u, 0x14292967u,
    0x27b70a85u, 0x2e1b2138u, 0x4d2c6dfcu, 0x53380d13u, 0x650a7354u, 0x766a0abbu, 0x81c2c92eu, 0x92722c85u,
    0xa2bfe8a1u, 0xa81a664bu, 0xc24b8b70u, 0xc76c51a3u, 0xd192e819u, 0xd6990624u, 0xf40e3585u, 0x106aa070u,
    0x19a4c116u, 0x1e376c08u, 0x2748774cu, 0x34b0bcb5u, 0x391c0cb3u, 0x4ed8aa4au, 0x5b9cca4fu, 0x682e6ff3u,
    0x748f82eeu, 0x78a5636fu, 0x84c87814u, 0x8cc70208u, 0x90befffau, 0xa4506cebu, 0xbef9a3f7u, 0xc67178f2u};
U32 rotate(U32 x, U32 n) { return (x >> n) | (x << (32 - n)); }
U32 big32(const Byte* p) { return U32(p[0]) << 24 | U32(p[1]) << 16 | U32(p[2]) << 8 | U32(p[3]); }
// Boot images are hashed from uncached external memory, where every access is
// expensive: whole blocks are read in place, a word at a time when aligned.
static_assert(__BYTE_ORDER__ == __ORDER_LITTLE_ENDIAN__, "word loads assume little-endian");
typedef U32 __attribute__((may_alias)) AliasU32;
U32 swap32(U32 w) { return w >> 24 | (w >> 8 & 0xff00u) | (w << 8 & 0xff0000u) | w << 24; }
U32 sigma0(U32 x) { return rotate(x, 7) ^ rotate(x, 18) ^ (x >> 3); }
U32 sigma1(U32 x) { return rotate(x, 17) ^ rotate(x, 19) ^ (x >> 10); }
// #279: one round. Eight in a row, each with the state's names moved along by one, leave no state to move between
// rounds: d and h are the two words a round changes.
__attribute__((always_inline)) inline void sha_round(U32 a, U32 b, U32 c, U32& d, U32 e, U32 f, U32 g, U32& h, U32 kw) {
    const U32 x = h + (rotate(e, 6) ^ rotate(e, 11) ^ rotate(e, 25)) + (g ^ (e & (f ^ g))) + kw;
    d += x;
    h = x + (rotate(a, 2) ^ rotate(a, 13) ^ rotate(a, 22)) + ((a & b) | (c & (a | b)));
}
void put32(Byte* p, U32 value) {
    p[0] = Byte(value >> 24);
    p[1] = Byte(value >> 16);
    p[2] = Byte(value >> 8);
    p[3] = Byte(value);
}
}
bool equal(const Digest& a, const Digest& b) {
    U32 different = 0;
    for (U32 i = 0; i < 32; ++i) different |= a.bytes[i] ^ b.bytes[i];
    return different == 0;
}
Sha256::Sha256() { reset(); }
void Sha256::reset() {
    const U32 initial[8] = {0x6a09e667u, 0xbb67ae85u, 0x3c6ef372u, 0xa54ff53au,
                            0x510e527fu, 0x9b05688cu, 0x1f83d9abu, 0x5be0cd19u};
    for (U32 i = 0; i < 8; ++i) state_[i] = initial[i];
    low_ = high_ = used_ = 0;
    finished_ = false;
}
bool Sha256::update(const Byte* data, U32 count) {
    if (finished_ || (!data && count)) return false;
    const U32 low = low_ + count, high = high_ + U32(low < low_);
    if (high >= 0x20000000u) return false;
    low_ = low;
    high_ = high;
    U32 i = 0;
    if (used_) {                                   // finish a partly filled block first
        while (used_ < 64 && i < count) block_[used_++] = data[i++];
        if (used_ < 64) return true;
        transform(block_);
        used_ = 0;
    }
    for (; count - i >= 64; i += 64) transform(data + i);    // whole blocks straight from the input
    while (i < count) block_[used_++] = data[i++];
    return true;
}
void Sha256::transform(const Byte* block) {
    U32 w[64];                                     // #279: the whole message schedule first, then the rounds
    if ((reinterpret_cast<unsigned long>(block) & 3u) == 0) {
        const AliasU32* in = reinterpret_cast<const AliasU32*>(block);
        for (U32 i = 0; i < 16; ++i) w[i] = swap32(in[i]);
    } else {
        for (U32 i = 0; i < 16; ++i) w[i] = big32(block + 4 * i);
    }
    for (U32 i = 16; i < 64; ++i) w[i] = w[i - 16] + sigma0(w[i - 15]) + w[i - 7] + sigma1(w[i - 2]);
    U32 a = state_[0], b = state_[1], c = state_[2], d = state_[3], e = state_[4], f = state_[5], g = state_[6],
        h = state_[7];
    for (U32 i = 0; i < 64; i += 8) {
        sha_round(a, b, c, d, e, f, g, h, constants[i] + w[i]);
        sha_round(h, a, b, c, d, e, f, g, constants[i + 1] + w[i + 1]);
        sha_round(g, h, a, b, c, d, e, f, constants[i + 2] + w[i + 2]);
        sha_round(f, g, h, a, b, c, d, e, constants[i + 3] + w[i + 3]);
        sha_round(e, f, g, h, a, b, c, d, constants[i + 4] + w[i + 4]);
        sha_round(d, e, f, g, h, a, b, c, constants[i + 5] + w[i + 5]);
        sha_round(c, d, e, f, g, h, a, b, constants[i + 6] + w[i + 6]);
        sha_round(b, c, d, e, f, g, h, a, constants[i + 7] + w[i + 7]);
    }
    state_[0] += a;
    state_[1] += b;
    state_[2] += c;
    state_[3] += d;
    state_[4] += e;
    state_[5] += f;
    state_[6] += g;
    state_[7] += h;
}
bool Sha256::finish(Digest& digest) {
    if (finished_) return false;
    block_[used_++] = 0x80;
    if (used_ > 56) {
        while (used_ < 64) block_[used_++] = 0;
        transform(block_);
        used_ = 0;
    }
    while (used_ < 56) block_[used_++] = 0;
    put32(block_ + 56, (high_ << 3) | (low_ >> 29));
    put32(block_ + 60, low_ << 3);
    transform(block_);
    for (U32 i = 0; i < 8; ++i) put32(digest.bytes + 4 * i, state_[i]);
    finished_ = true;
    return true;
}
}}
