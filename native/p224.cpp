#include "p224.hpp"
namespace xyf_core { namespace p224 {
namespace {
// FIPS 186-4 D.1.2.2, big-endian. The 1.2.1 bootloader's verifier holds the same values
// (0x11b00ea4 p, 0x11b00edc b, 0x11b00ef8 Gx, 0x11b00f14 Gy, 0x11b00f30 n); a = -3.
const Byte p_bytes[28] = {
    0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,
    0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x01};
const Byte b_bytes[28] = {
    0xb4,0x05,0x0a,0x85,0x0c,0x04,0xb3,0xab,0xf5,0x41,0x32,0x56,0x50,0x44,0xb0,0xb7,
    0xd7,0xbf,0xd8,0xba,0x27,0x0b,0x39,0x43,0x23,0x55,0xff,0xb4};
const Byte gx_bytes[28] = {
    0xb7,0x0e,0x0c,0xbd,0x6b,0xb4,0xbf,0x7f,0x32,0x13,0x90,0xb9,0x4a,0x03,0xc1,0xd3,
    0x56,0xc2,0x11,0x22,0x34,0x32,0x80,0xd6,0x11,0x5c,0x1d,0x21};
const Byte gy_bytes[28] = {
    0xbd,0x37,0x63,0x88,0xb5,0xf7,0x23,0xfb,0x4c,0x22,0xdf,0xe6,0xcd,0x43,0x75,0xa0,
    0x5a,0x07,0x47,0x64,0x44,0xd5,0x81,0x99,0x85,0x00,0x7e,0x34};
const Byte n_bytes[28] = {
    0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0x16,0xa2,
    0xe0,0xb8,0xf0,0x3e,0x13,0xdd,0x29,0x45,0x5c,0x5c,0x2a,0x3d};

// A 224-bit number as 14 16-bit digits, least significant first, one per U32: a digit
// product plus two digits is at most 2^32 - 1, so no wider type (and no library multiply)
// is ever needed.
const U32 digits = 14, bits = 224, mask = 0xffffu;
struct Number { U32 d[digits]; };
// An odd modulus m with 2^223 < m < 2^224 (p and n both qualify), with R = 2^224:
// rr = R^2 mod m and inverse = -m^-1 mod 2^16, for Montgomery multiplication.
struct Modulus { Number m, rr; U32 inverse; };
struct Point { Number x, y, z; };          // Jacobian (x/z^2, y/z^3); z = 0 is infinity

void set(Number& out, U32 value) {
    for (U32 i = 0; i < digits; ++i) out.d[i] = 0;
    out.d[0] = value & mask;
    out.d[1] = value >> 16;
}
void copy(Number& out, const Number& a) { for (U32 i = 0; i < digits; ++i) out.d[i] = a.d[i]; }
void copy(Point& out, const Point& a) { copy(out.x, a.x); copy(out.y, a.y); copy(out.z, a.z); }
void load(Number& out, const Byte* big_endian) {   // 28 bytes
    for (U32 i = 0; i < digits; ++i)
        out.d[i] = U32(big_endian[27 - 2 * i]) | U32(big_endian[26 - 2 * i]) << 8;
}
bool is_zero(const Number& a) {
    U32 any = 0;
    for (U32 i = 0; i < digits; ++i) any |= a.d[i];
    return any == 0;
}
bool equal(const Number& a, const Number& b) {
    U32 different = 0;
    for (U32 i = 0; i < digits; ++i) different |= a.d[i] ^ b.d[i];
    return different == 0;
}
bool less(const Number& a, const Number& b) {      // a < b
    for (U32 i = digits; i-- > 0;)
        if (a.d[i] != b.d[i]) return a.d[i] < b.d[i];
    return false;
}
bool bit(const Number& a, U32 index) { return (a.d[index >> 4] >> (index & 15)) & 1; }
// out = a + b mod 2^224; returns the carry out. out may be a or b.
U32 add(Number& out, const Number& a, const Number& b) {
    U32 carry = 0;
    for (U32 i = 0; i < digits; ++i) {
        const U32 sum = a.d[i] + b.d[i] + carry;
        out.d[i] = sum & mask;
        carry = sum >> 16;
    }
    return carry;
}
// out = a - b mod 2^224; returns the borrow out. out may be a or b.
U32 subtract(Number& out, const Number& a, const Number& b) {
    U32 borrow = 0;
    for (U32 i = 0; i < digits; ++i) {
        const U32 difference = a.d[i] - b.d[i] - borrow;
        out.d[i] = difference & mask;
        borrow = (difference >> 16) & 1;
    }
    return borrow;
}
// Modular sum and difference of a, b < m.
void add_mod(Number& out, const Number& a, const Number& b, const Modulus& m) {
    if (add(out, a, b) || !less(out, m.m)) subtract(out, out, m.m);
}
void subtract_mod(Number& out, const Number& a, const Number& b, const Modulus& m) {
    if (subtract(out, a, b)) add(out, out, m.m);
}
// out = a * b / R mod m for a, b < m (Montgomery multiplication, coarsely integrated
// operand scanning). t holds the running sum: digits + 2 places, each below 2^16 between
// steps. out may be a or b.
void montgomery(Number& out, const Number& a, const Number& b, const Modulus& m) {
    U32 t[digits + 2];
    for (U32 i = 0; i < digits + 2; ++i) t[i] = 0;
    for (U32 i = 0; i < digits; ++i) {
        U32 carry = 0;                                  // t += a * b[i]
        for (U32 j = 0; j < digits; ++j) {
            const U32 x = t[j] + a.d[j] * b.d[i] + carry;
            t[j] = x & mask;
            carry = x >> 16;
        }
        U32 x = t[digits] + carry;
        t[digits] = x & mask;
        t[digits + 1] = x >> 16;
        const U32 q = (t[0] * m.inverse) & mask;       // t + q*m is divisible by 2^16
        x = t[0] + q * m.m.d[0];
        carry = x >> 16;
        for (U32 j = 1; j < digits; ++j) {              // t = (t + q*m) / 2^16
            x = t[j] + q * m.m.d[j] + carry;
            t[j - 1] = x & mask;
            carry = x >> 16;
        }
        x = t[digits] + carry;
        t[digits - 1] = x & mask;
        t[digits] = t[digits + 1] + (x >> 16);
    }
    Number result;                                      // t < 2m: one subtraction at most
    for (U32 i = 0; i < digits; ++i) result.d[i] = t[i];
    if (t[digits] || !less(result, m.m)) subtract(result, result, m.m);
    copy(out, result);
}
// out = a * b mod m for a, b < m: (a*b/R) * R^2 / R. out may be a or b.
void multiply(Number& out, const Number& a, const Number& b, const Modulus& m) {
    Number t;
    montgomery(t, a, b, m);
    montgomery(out, t, m.rr, m);
}
// out = a^(m-2) mod m = a^-1 mod m for 0 < a < m, m prime (Fermat); 0 for a = 0.
void invert(Number& out, const Number& a, const Modulus& m) {
    Number exponent, two, result;
    set(two, 2);
    subtract(exponent, m.m, two);
    set(result, 1);
    for (U32 i = bits; i-- > 0;) {                     // left to right; the exponent is public
        multiply(result, result, result, m);
        if (bit(exponent, i)) multiply(result, result, a, m);
    }
    copy(out, result);
}
void prepare(Modulus& m, const Byte* big_endian) {
    load(m.m, big_endian);
    U32 x = 1;                                          // Newton: x = m^-1 mod 2^16, one
    for (U32 i = 0; i < 4; ++i)                         // correct low bit doubling to 16
        x = (x * (2 - m.m.d[0] * x)) & mask;
    m.inverse = (0x10000u - x) & mask;
    Number zero;
    set(zero, 0);
    subtract(m.rr, zero, m.m);                          // R mod m = 2^224 - m (m > 2^223)
    for (U32 i = 0; i < bits; ++i) add_mod(m.rr, m.rr, m.rr, m);   // * 2^224: R^2 mod m
}

// The curve y^2 = x^3 - 3x + b over p, Jacobian coordinates, formulas from the Explicit-
// Formulas Database (hyperelliptic.org/EFD, short Weierstrass a = -3, Jacobian).
struct Curve { Modulus p; Number b; };
// dbl-2001-b. out may be a.
void twice(Point& out, const Point& a, const Curve& c) {
    if (is_zero(a.z)) { copy(out, a); return; }
    const Modulus& p = c.p;
    Number delta, gamma, beta, alpha, t, u;
    multiply(delta, a.z, a.z, p);                       // delta = Z1^2
    multiply(gamma, a.y, a.y, p);                       // gamma = Y1^2
    multiply(beta, a.x, gamma, p);                      // beta = X1*gamma
    subtract_mod(t, a.x, delta, p);
    add_mod(u, a.x, delta, p);
    multiply(alpha, t, u, p);
    add_mod(t, alpha, alpha, p);
    add_mod(alpha, t, alpha, p);                        // alpha = 3*(X1-delta)*(X1+delta)
    add_mod(t, a.y, a.z, p);
    multiply(t, t, t, p);
    subtract_mod(t, t, gamma, p);
    subtract_mod(out.z, t, delta, p);                   // Z3 = (Y1+Z1)^2-gamma-delta
    add_mod(beta, beta, beta, p);
    add_mod(beta, beta, beta, p);                       // beta = 4*beta
    multiply(t, alpha, alpha, p);
    add_mod(u, beta, beta, p);
    subtract_mod(out.x, t, u, p);                       // X3 = alpha^2-8*beta
    subtract_mod(t, beta, out.x, p);
    multiply(t, alpha, t, p);
    multiply(gamma, gamma, gamma, p);
    add_mod(gamma, gamma, gamma, p);
    add_mod(gamma, gamma, gamma, p);
    add_mod(gamma, gamma, gamma, p);                    // 8*gamma^2
    subtract_mod(out.y, t, gamma, p);                   // Y3 = alpha*(4*beta-X3)-8*gamma^2
}
// add-1998-cmo-2, with the cases it excludes handled first: either input at infinity,
// equal inputs (doubling) and opposite inputs (infinity). out may be a or b.
void sum(Point& out, const Point& a, const Point& b, const Curve& c) {
    if (is_zero(a.z)) { copy(out, b); return; }
    if (is_zero(b.z)) { copy(out, a); return; }
    const Modulus& p = c.p;
    Number z1z1, z2z2, u1, u2, s1, s2, t;
    multiply(z1z1, a.z, a.z, p);
    multiply(z2z2, b.z, b.z, p);
    multiply(u1, a.x, z2z2, p);                         // U1 = X1*Z2^2
    multiply(u2, b.x, z1z1, p);                         // U2 = X2*Z1^2
    multiply(t, b.z, z2z2, p);
    multiply(s1, a.y, t, p);                            // S1 = Y1*Z2^3
    multiply(t, a.z, z1z1, p);
    multiply(s2, b.y, t, p);                            // S2 = Y2*Z1^3
    subtract_mod(u2, u2, u1, p);                        // H = U2-U1 (in u2)
    subtract_mod(s2, s2, s1, p);                        // r = S2-S1 (in s2)
    if (is_zero(u2)) {
        if (is_zero(s2)) { twice(out, a, c); return; }  // the same point
        set(out.x, 1); set(out.y, 1); set(out.z, 0);    // opposite points: infinity
        return;
    }
    Number hh, hhh;
    multiply(hh, u2, u2, p);                            // HH = H^2
    multiply(hhh, u2, hh, p);                           // HHH = H*HH
    multiply(u1, u1, hh, p);                            // V = U1*HH (in u1)
    multiply(t, a.z, b.z, p);
    multiply(out.z, t, u2, p);                          // Z3 = Z1*Z2*H
    multiply(t, s2, s2, p);
    subtract_mod(t, t, hhh, p);
    subtract_mod(t, t, u1, p);
    subtract_mod(out.x, t, u1, p);                      // X3 = r^2-HHH-2*V
    subtract_mod(t, u1, out.x, p);
    multiply(t, s2, t, p);
    multiply(s1, s1, hhh, p);
    subtract_mod(out.y, t, s1, p);                      // Y3 = r*(V-X3)-S1*HHH
}
// y^2 = x^3 - 3x + b, for x, y < p.
bool on_curve(const Number& x, const Number& y, const Curve& c) {
    Number left, right, t;
    multiply(left, y, y, c.p);
    multiply(t, x, x, c.p);
    multiply(right, t, x, c.p);
    add_mod(t, x, x, c.p);
    add_mod(t, t, x, c.p);
    subtract_mod(right, right, t, c.p);
    add_mod(right, right, c.b, c.p);
    return equal(left, right);
}
// Steps 2-4 of SEC 1 4.1.4: e = the digest's leftmost 224 bits as an integer, mod n;
// w = s^-1; u1 = e*w, u2 = r*w (mod n). Separate functions keep every frame small.
__attribute__((noinline)) void scalars(Number& u1, Number& u2, const Byte* digest, U32 digest_bytes,
                                       const Number& r, const Number& s, const Modulus& n) {
    Byte leftmost[28];
    const U32 used = digest_bytes < 28 ? digest_bytes : 28;
    for (U32 i = 0; i < 28; ++i) leftmost[i] = i < 28 - used ? 0 : digest[i - (28 - used)];
    Number e, w;
    load(e, leftmost);
    if (!less(e, n.m)) subtract(e, e, n.m);              // e < 2^224 < 2n
    invert(w, s, n);
    multiply(u1, e, w, n);
    multiply(u2, r, w, n);
}
// Step 5: x = the affine x of u1*G + u2*Q (Shamir's trick: one doubling per bit, then add
// G, Q or G+Q); false for the point at infinity.
__attribute__((noinline)) bool combine(Number& x, const Number& u1, const Number& u2, const Point& q,
                                       const Curve& c) {
    Point g, both, result;
    load(g.x, gx_bytes);
    load(g.y, gy_bytes);
    set(g.z, 1);
    sum(both, g, q, c);
    set(result.x, 1); set(result.y, 1); set(result.z, 0);
    for (U32 i = bits; i-- > 0;) {
        twice(result, result, c);
        const U32 pick = U32(bit(u1, i)) | U32(bit(u2, i)) << 1;
        if (pick) sum(result, result, pick == 1 ? g : pick == 2 ? q : both, c);
    }
    if (is_zero(result.z)) return false;
    multiply(x, result.z, result.z, c.p);                // x = X / Z^2
    invert(x, x, c.p);
    multiply(x, result.x, x, c.p);
    return true;
}
}

Status verify(const Byte* public_key, const Byte* digest, U32 digest_bytes, const Byte* r_bytes, const Byte* s_bytes) {
    if (!public_key || !digest || !r_bytes || !s_bytes || !digest_bytes || digest_bytes > 64) return Status::argument;
    Curve c;
    prepare(c.p, p_bytes);
    load(c.b, b_bytes);
    Modulus n;
    prepare(n, n_bytes);
    Point q;                                             // SEC 1 3.2.2.1: Q in range, on the curve
    load(q.x, public_key);
    load(q.y, public_key + 28);
    set(q.z, 1);
    if (!less(q.x, c.p.m) || !less(q.y, c.p.m) || !on_curve(q.x, q.y, c)) return Status::key;
    Number r, s;                                         // 4.1.4 step 1: r, s in [1, n-1]
    load(r, r_bytes);
    load(s, s_bytes);
    if (is_zero(r) || is_zero(s) || !less(r, n.m) || !less(s, n.m)) return Status::range;
    Number u1, u2, x;
    scalars(u1, u2, digest, digest_bytes, r, s, n);      // steps 2-4
    if (!combine(x, u1, u2, q, c)) return Status::mismatch;   // step 5: R is not infinity
    if (!less(x, n.m)) subtract(x, x, n.m);              // steps 6-7: x_R mod n (x < p < 2n) == r
    return equal(x, r) ? Status::valid : Status::mismatch;
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
} }
