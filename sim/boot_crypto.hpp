#ifndef XYF_SIM_BOOT_CRYPTO_HPP
#define XYF_SIM_BOOT_CRYPTO_HPP
#include <stdint.h>
#include <string.h>
namespace xyf_sim {
// Deliberately NOT AES or OTP-derived. A reversible representation of stock's in-place
// encryption, covering every padded sector. The reserved header marker prevents treating
// real ciphertext (or an older plaintext simulator file) as this representation.
inline void boot_crypt(uint8_t* data, uint32_t bytes, uint32_t offset = 0) {
    for (uint32_t i = 0; i != bytes; ++i) data[i] ^= uint8_t(0xa5u + (offset + i) * 29u);
}
inline void boot_crypto_mark(uint8_t* header) { header[13] = 1; memcpy(header + 14, "XYFSIM01", 8); }
inline bool boot_crypto_model(const uint8_t* header) {
    return header[13] == 1 && memcmp(header + 14, "XYFSIM01", 8) == 0;
}
}
#endif
