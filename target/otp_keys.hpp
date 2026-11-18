#ifndef XYF_OTP_KEYS_HPP
#define XYF_OTP_KEYS_HPP
#include "../native/ldr.hpp"
// The device's own .tfw keys, read from processor B's one-time-programmable memory (OTP) through
// the two boot-ROM entries the stock 1.2.1 bootloader uses (docs/hardware/tfw.md,
// docs/hardware/tfw.md "OTP"). Read only: no OTP write or program entry is linked (the builder's ROM
// rule admits only these two and the ROM memcpy). Used only by `tfw.check` (XYF_TFW_CHECK), which
// passes the functions to native/tfw_check as its keys.
// Each function writes the key into `out`; the ROM's scratch is a stack buffer, cleared before
// each read as stock clears its own. (Keys on the device need no protection; they never go into the repository.)
namespace xy_forever { namespace otp_keys {
static const xyf_core::U32 public_key_bytes = 56, aes_key_bytes = 32;
// The vendor public key (signature key index 0): selector 9, Qx || Qy big-endian, as stock
// 11a05b22 reads it. false: the ROM did not answer (out is all zero).
bool public_key(void* context, xyf_core::Byte out[56]);
// The package AES key: index 0 only, selectors 3 and 4 (16 bytes each), as stock 11a05c9c reads
// it. Index 1 (selectors 5/6) is refused unread. false: refused or no answer (out is all zero).
bool aes_key(void* context, xyf_core::U32 index, xyf_core::Byte out[32]);
} }
#endif
