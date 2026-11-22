#include "p224.hpp"
// micro-ecc (third_party/micro-ecc: version, licence and the two local changes in its README),
// built for the Blackfin: 32-bit words, P-224 only, no point compression. Verification only;
// signing and key generation stay unused and are dropped by the link's --gc-sections.
#include <stdint.h>
#define XYF_UECC_CXX_LINKAGE 1              // C++ linkage: the freestanding audit sees no outside calls
#define uECC_WORD_SIZE 4
#define uECC_SUPPORTS_secp160r1 0
#define uECC_SUPPORTS_secp192r1 0
#define uECC_SUPPORTS_secp224r1 1
#define uECC_SUPPORTS_secp256r1 0
#define uECC_SUPPORTS_secp256k1 0
#define uECC_SUPPORT_COMPRESSED_POINT 0
// micro-ecc's port hook for a platform multiply (its ARM and AVR assembly use it): its generic one
// forms a 64-bit product, which the Blackfin's GCC turns into a libgcc __muldi3 call.
#define asm_mult 1
#include "../third_party/micro-ecc/uECC.h"
#include "../third_party/micro-ecc/uECC_vli.h"
namespace {
// r2:r1:r0 += a * b from four 16 x 16-bit products, so every intermediate fits a uint32_t.
void multiply_add(uECC_word_t a, uECC_word_t b, uECC_word_t& r0, uECC_word_t& r1, uECC_word_t& r2) {
    const uECC_word_t a0 = a & 0xffffu, a1 = a >> 16, b0 = b & 0xffffu, b1 = b >> 16;
    const uECC_word_t cross = a0 * b1, middle = cross + a1 * b0;
    uECC_word_t low = a0 * b0, high = a1 * b1 + (uECC_word_t(middle < cross) << 16) + (middle >> 16);
    const uECC_word_t sum = low + (middle << 16);
    high += sum < low;
    low = sum;
    r0 += low;
    const uECC_word_t carry = r0 < low;
    r1 += high;
    uECC_word_t over = r1 < high;
    r1 += carry;
    over += r1 < carry;
    r2 += over;
}
}
// The product scan of micro-ecc's own uECC_vli_mult, over multiply_add.
static void uECC_vli_mult(uECC_word_t* result, const uECC_word_t* left, const uECC_word_t* right,
                          wordcount_t num_words) {
    uECC_word_t r0 = 0, r1 = 0, r2 = 0;
    for (wordcount_t k = 0; k < num_words * 2 - 1; ++k) {
        const wordcount_t first = k < num_words ? 0 : wordcount_t(k + 1 - num_words);
        for (wordcount_t i = first; i <= k && i < num_words; ++i) multiply_add(left[i], right[k - i], r0, r1, r2);
        result[k] = r0;
        r0 = r1;
        r1 = r2;
        r2 = 0;
    }
    result[num_words * 2 - 1] = r0;
}
#if !__STDC_HOSTED__
// Freestanding (the firmware), micro-ecc knows no RNG; one that always fails keeps its RNG pointer
// out of BSS. Verification draws no random numbers.
static int default_RNG(uint8_t*, unsigned) { return 0; }
#define default_RNG_defined 1
#endif
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wunused-parameter"
#pragma GCC diagnostic ignored "-Wsign-compare"
#include "../third_party/micro-ecc/uECC.c"
#pragma GCC diagnostic pop

namespace xyf_core { namespace p224 {
namespace {
bool in_range(const Byte* scalar, uECC_Curve curve) {          // 1 <= scalar <= n - 1
    uECC_word_t value[uECC_MAX_WORDS];
    uECC_vli_bytesToNative(value, scalar, curve->num_bytes);
    return !uECC_vli_isZero(value, curve->num_words) && uECC_vli_cmp_unsafe(curve->n, value, curve->num_words) == 1;
}
}

Status verify(const Byte* public_key, const Byte* digest, U32 digest_bytes, const Byte* r, const Byte* s) {
    if (!public_key || !digest || !r || !s || !digest_bytes || digest_bytes > 64) return Status::argument;
    const uECC_Curve curve = uECC_secp224r1();
    if (!uECC_valid_public_key(public_key, curve)) return Status::key;       // SEC 1 3.2.2.1
    if (!in_range(r, curve) || !in_range(s, curve)) return Status::range;     // SEC 1 4.1.4 step 1
    Byte signature[2 * scalar_bytes];
    for (U32 i = 0; i < scalar_bytes; ++i) {
        signature[i] = r[i];
        signature[scalar_bytes + i] = s[i];
    }
    return uECC_verify(public_key, digest, digest_bytes, signature, curve) ? Status::valid : Status::mismatch;
}
const char* status_name(Status status) {
    switch (status) {
    case Status::valid: return "valid";
    case Status::argument: return "argument";
    case Status::key: return "key";
    case Status::range: return "range";
    case Status::mismatch: return "mismatch";
    }
    return "unknown";
}
}}
