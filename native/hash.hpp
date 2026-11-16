#ifndef XYF_HASH_HPP
#define XYF_HASH_HPP
#include "ldr.hpp"
namespace xyf_core { namespace hash {
struct Digest { Byte bytes[32]; };
bool equal(const Digest& a, const Digest& b);
// SHA-256 as specified in FIPS 180-4 / RFC 6234. No keys or device access.
class Sha256 {
public:
    Sha256();
    void reset();
    bool update(const Byte* bytes, U32 count);
    bool finish(Digest& digest);
private:
    void transform(const Byte* block);
    U32 state_[8], low_, high_, used_;
    Byte block_[64];
    bool finished_;
};
} }
#endif
