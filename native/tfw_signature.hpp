#ifndef XYF_TFW_SIGNATURE_HPP
#define XYF_TFW_SIGNATURE_HPP
#include "p224.hpp"
#include "tfw.hpp"
namespace xyf_core { namespace tfw {
// The stock manifest signature check (1.2.1 bootloader 88a17f2a), natively and
// without touching the header. Over the 0x300-byte crypto header:
//   - signature key index (+0x89) must be 0, the vendor ("cloud") key; stock's 1 (per-device)
//     and 0xff (dev bring-up) keys need its dev mode and are refused here;
//   - message: SHA-256 of the header with +0x10..+0x50 read as zero; ECDSA P-224 uses its
//     leftmost 224 bits;
//   - r = +0x14..+0x30 and s = +0x34..+0x50, 28-byte big-endian;
//   - public key: Qx || Qy, 28-byte big-endian each (OTP selector 9, 56 bytes).
// The header binds the payload: its SHA-256 of the whole ciphertext (+0x9c) and the sizes
// are signed, and Archive::open_digested compares that hash after this check accepts.
static const U32 signature_hole_begin = 0x10, signature_hole_end = 0x50, signature_r = 0x14, signature_s = 0x34,
                 signature_key_index = 0x89, signature_public_key_bytes = p224::public_key_bytes;
enum class Signature { valid, size, key_index, key, range, mismatch, argument };
Signature verify_header(const Byte* crypto_header, U32 size, const Byte* public_key);
// The message digest stock signs (the hole read as zero); for tests.
void signed_digest(const Byte* crypto_header, hash::Digest& digest);
// A HeaderCheck that runs verify_header with `public_key` (56 bytes, kept by the caller).
HeaderCheck signature_check(const Byte* public_key);
const char* signature_name(Signature result);
}}
#endif
