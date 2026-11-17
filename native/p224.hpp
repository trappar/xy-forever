#ifndef XYF_P224_HPP
#define XYF_P224_HPP
#include "ldr.hpp"
namespace xyf_core { namespace p224 {
// ECDSA signature verification over NIST P-224 (FIPS 186-4 sections 6.4 and D.1.2.2; SEC 1 v2
// section 4.1.4), for the .tfw header check (tfw::verify_header). Verification only:
// public inputs, no secrets, no signing. Written to be read: textbook Jacobian formulas over
// plain field elements, one generic modular multiply (Montgomery, 16-bit digits, so every
// product and sum fits a U32), inverses by Fermat. No heap, no library calls, no recursion.
static const U32 scalar_bytes = 28, public_key_bytes = 56;
// key: a coordinate >= p, or the point is not on the curve (the all-zero encoding of the point
// at infinity is not on it); range: r or s outside [1, n-1]; mismatch: u1*G + u2*Q is the
// point at infinity or its x mod n differs from r; argument: a null pointer or an empty or
// longer-than-64-byte digest.
enum class Status { valid, argument, key, range, mismatch };
// public_key: Qx || Qy, each 28-byte big-endian (the stock OTP selector-9 layout).
// r, s: 28-byte big-endian. digest: the message hash; its leftmost 224 bits are used
// (all of it when shorter), as FIPS 186-4 6.4 specifies.
Status verify(const Byte* public_key, const Byte* digest, U32 digest_bytes, const Byte* r, const Byte* s);
const char* status_name(Status status);
} }
#endif
