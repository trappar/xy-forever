// ctypes bridge for native/p224 and native/tfw_signature (tests/test_p224.py).
#include "../native/tfw_signature.hpp"
#include <chrono>
using namespace xyf_core;
extern "C" int p224_verify(const unsigned char* key, const unsigned char* digest, unsigned int digest_bytes,
                           const unsigned char* r, const unsigned char* s) {
    return int(p224::verify(key, digest, digest_bytes, r, s));
}
extern "C" int tfw_verify_header(const unsigned char* header, unsigned int size, const unsigned char* key) {
    return int(tfw::verify_header(header, size, key));
}
extern "C" void tfw_signed_digest(const unsigned char* header, unsigned char* out) {
    hash::Digest digest;
    tfw::signed_digest(header, digest);
    for (unsigned int i = 0; i < 32; ++i) out[i] = digest.bytes[i];
}
// Runs `count` verifications of one input; returns the microseconds per verification.
extern "C" double p224_time(const unsigned char* key, const unsigned char* digest, const unsigned char* r,
                            const unsigned char* s, unsigned int count) {
    const auto start = std::chrono::steady_clock::now();
    for (unsigned int i = 0; i < count; ++i)
        if (p224::verify(key, digest, 32, r, s) != p224::Status::valid) return -1;
    return std::chrono::duration<double, std::micro>(std::chrono::steady_clock::now() - start).count() / count;
}
