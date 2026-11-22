#include "aes.hpp"
// rijndael-alg-fst (third_party/rijndael-fst: version, licence and its one local change in the README): the
// table-driven AES reference code, AES-256 decryption only (the .tfw reader runs its own random-access CBC over
// single blocks). Included in an unnamed namespace, so its unused encryption and tables are dropped.
#include <stdint.h>
namespace {
typedef uint8_t u8;
typedef uint32_t u32;
#define XYF_RIJNDAEL_FREESTANDING 1
#define KASSERT(exp, msg)
// The round constants computed, not a table: rcon[2] (0x04000000) is a boot-ROM address to the builder's audit of
// stored words (xy_forever/otp_audit.py).
#define XYF_RIJNDAEL_RCON 1
struct RoundConstants {
    u32 operator[](int i) const {
        u32 c = 1;
        while (i-- > 0) c = (c << 1) ^ (c & 0x80u ? 0x11bu : 0u);
        return c << 24;
    }
};
const RoundConstants rcon = {};
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wunused-function"
#include "../third_party/rijndael-fst/rijndael-alg-fst.c"
#pragma GCC diagnostic pop
#undef KASSERT
}

namespace xyf_core { namespace crypto {
Aes256::Aes256() : ready_(false) {}
Aes256::~Aes256() { erase(); }
void Aes256::erase() {
    volatile uint32_t* key = schedule_;
    for (U32 i = 0; i < sizeof(schedule_) / 4; ++i) key[i] = 0;
    ready_ = false;
}
bool Aes256::initialize(const Byte* key) {
    erase();
    if (!key) return false;
    ready_ = rijndaelKeySetupDec(schedule_, key, 256) == rounds;
    return ready_;
}
bool Aes256::decrypt(const Byte* input, Byte* output) const {
    if (!ready_ || !input || !output) return false;
    rijndaelDecrypt(schedule_, rounds, input, output);
    return true;
}
}}
