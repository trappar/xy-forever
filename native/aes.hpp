#ifndef XYF_AES_HPP
#define XYF_AES_HPP
#include "ldr.hpp"
namespace xyf_core { namespace crypto {
// AES-256 block decryption, FIPS 197, by tiny-AES-c (native/aes.cpp). Schedule is transient and
// explicitly erased.
class Aes256 {
public:
    Aes256();
    ~Aes256();
    bool initialize(const Byte* key32);
    bool decrypt(const Byte* input16, Byte* output16) const;
    void erase();

private:
    Aes256(const Aes256&);
    Aes256& operator=(const Aes256&);
    Byte schedule_[240];
    bool ready_;
};
}}
#endif
