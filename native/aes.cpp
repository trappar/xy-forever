#include "aes.hpp"
// tiny-AES-c (third_party/tiny-AES-c: version, licence and its one local change in the README):
// AES-256, ECB only (the .tfw reader runs its own random-access CBC over single blocks).
#define XYF_TINY_AES_FREESTANDING 1
#define AES256 1
#define ECB 1
#define CBC 0
#define CTR 0
#include "../third_party/tiny-AES-c/aes.c"

namespace xyf_core { namespace crypto {
Aes256::Aes256() : ready_(false) {}
Aes256::~Aes256() { erase(); }
void Aes256::erase() {
    volatile Byte* key = schedule_;
    for (U32 i = 0; i < sizeof(schedule_); ++i) key[i] = 0;
    ready_ = false;
}
bool Aes256::initialize(const Byte* key) {
    static_assert(sizeof(schedule_) == AES_keyExpSize, "the AES-256 key schedule");
    erase();
    if (!key) return false;
    KeyExpansion(schedule_, key);                   // what AES_init_ctx does, into our schedule
    ready_ = true;
    return true;
}
bool Aes256::decrypt(const Byte* input, Byte* output) const {
    if (!ready_ || !input || !output) return false;
    state_t state;
    Byte* bytes = &state[0][0];
    for (U32 i = 0; i < 16; ++i) bytes[i] = input[i];
    InvCipher(&state, schedule_);                   // what AES_ECB_decrypt does, in place
    for (U32 i = 0; i < 16; ++i) output[i] = bytes[i];
    volatile Byte* wipe = bytes;
    for (U32 i = 0; i < 16; ++i) wipe[i] = 0;
    return true;
}
}}
