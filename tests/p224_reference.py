"""A small, slow ECDSA P-224 reference written only for the tests (plan 4.3b).

Affine textbook arithmetic over Python integers (SEC 1 v2 section 4.1, FIPS 186-4): it signs
synthetic headers with synthetic keys, re-derives the RFC 6979 vectors and builds edge cases.
It is never used to decide anything about a real package: the device's own verifier
(native/p224.cpp) does that, and this file is the independent check of it.
"""
import hashlib

# FIPS 186-4 D.1.2.2 (curve P-224); the same values sit in the 1.2.1 bootloader at 0x11b00ea4.
P = 2 ** 224 - 2 ** 96 + 1
A = P - 3
B = 0xb4050a850c04b3abf54132565044b0b7d7bfd8ba270b39432355ffb4
N = 0xffffffffffffffffffffffffffff16a2e0b8f03e13dd29455c5c2a3d
G = (0xb70e0cbd6bb4bf7f321390b94a03c1d356c21122343280d6115c1d21,
     0xbd376388b5f723fb4c22dfe6cd4375a05a07476444d5819985007e34)
SIZE = 28


def on_curve(point):
    if point is None:
        return False
    x, y = point
    return 0 <= x < P and 0 <= y < P and (y * y - (x * x * x + A * x + B)) % P == 0


def add(p1, p2):
    if p1 is None:
        return p2
    if p2 is None:
        return p1
    (x1, y1), (x2, y2) = p1, p2
    if x1 == x2 and (y1 + y2) % P == 0:
        return None
    if p1 == p2:
        slope = (3 * x1 * x1 + A) * pow(2 * y1, -1, P) % P
    else:
        slope = (y2 - y1) * pow(x2 - x1, -1, P) % P
    x3 = (slope * slope - x1 - x2) % P
    return x3, (slope * (x1 - x3) - y1) % P


def multiply(k, point):
    result = None
    for bit in bin(k)[2:] if k > 0 else '':
        result = add(result, result)
        if bit == '1':
            result = add(result, point)
    return result


def negate(point):
    return None if point is None else (point[0], (-point[1]) % P)


def bits2int(digest):
    """The leftmost min(len, 224) bits of the digest as an integer (FIPS 186-4 6.4)."""
    value = int.from_bytes(digest, 'big')
    excess = len(digest) * 8 - 224
    return value >> excess if excess > 0 else value


def public_key(d):
    return multiply(d, G)


def sign(d, digest, k):
    """(r, s) for nonce k; raises if k gives r == 0 or s == 0."""
    r = multiply(k, G)[0] % N
    s = pow(k, -1, N) * (bits2int(digest) + r * d) % N
    if not r or not s:
        raise ValueError('degenerate nonce')
    return r, s


def verify(q, digest, r, s):
    if not on_curve(q) or not (0 < r < N and 0 < s < N):
        return False
    w = pow(s, -1, N)
    point = add(multiply(bits2int(digest) * w % N, G), multiply(r * w % N, q))
    return point is not None and point[0] % N == r


def sqrt_mod_p(value):
    """A square root modulo P (Tonelli-Shanks; P = 1 mod 2^96), or None."""
    value %= P
    if value == 0:
        return 0
    if pow(value, (P - 1) // 2, P) != 1:
        return None
    q, e = P - 1, 0
    while q % 2 == 0:
        q, e = q // 2, e + 1
    z = 2
    while pow(z, (P - 1) // 2, P) != P - 1:
        z += 1
    m, c, t, root = e, pow(z, q, P), pow(value, q, P), pow(value, (q + 1) // 2, P)
    while t != 1:
        i, t2 = 0, t
        while t2 != 1:
            t2, i = t2 * t2 % P, i + 1
        b = pow(c, 1 << (m - i - 1), P)
        m, c, t, root = i, b * b % P, t * b * b % P, root * b % P
    return root


def recover(digest, r, s):
    """Every public key for which (r, s) is a valid signature of digest (SEC 1 4.1.6)."""
    keys = []
    for x in (r, r + N):
        if x >= P:
            continue
        y = sqrt_mod_p(x * x * x + A * x + B)
        if y is None:
            continue
        for candidate in ((x, y), (x, P - y)):
            q = multiply(pow(r, -1, N), add(multiply(s, candidate), negate(multiply(bits2int(digest) % N, G))))
            if q is not None and verify(q, digest, r, s) and q not in keys:
                keys.append(q)
    return keys


def encode_point(point):
    """Qx || Qy, 28-byte big-endian each: the layout the stock verifier reads from OTP selector 9."""
    return point[0].to_bytes(SIZE, 'big') + point[1].to_bytes(SIZE, 'big')


def decode_point(data):
    return int.from_bytes(data[:SIZE], 'big'), int.from_bytes(data[SIZE:2 * SIZE], 'big')


# The signed message of a .tfw crypto header (stock 88a17f2a): the 0x300 bytes with +0x10..+0x50
# zeroed, SHA-256; r and s are 28-byte big-endian integers at +0x14 and +0x34.
HEADER_BYTES, SIGNED_HOLE, R_AT, S_AT = 0x300, (0x10, 0x50), 0x14, 0x34


def header_digest(header):
    header = bytearray(header[:HEADER_BYTES])
    header[SIGNED_HOLE[0]:SIGNED_HOLE[1]] = bytes(SIGNED_HOLE[1] - SIGNED_HOLE[0])
    return hashlib.sha256(bytes(header)).digest()


def header_signature(header):
    return (int.from_bytes(header[R_AT:R_AT + SIZE], 'big'), int.from_bytes(header[S_AT:S_AT + SIZE], 'big'))


def sign_header(header, d, k):
    """The header with r and s written in place (the rest of +0x10..+0x50 left as it was)."""
    r, s = sign(d, header_digest(header), k)
    signed = bytearray(header)
    signed[R_AT:R_AT + SIZE] = r.to_bytes(SIZE, 'big')
    signed[S_AT:S_AT + SIZE] = s.to_bytes(SIZE, 'big')
    return bytes(signed)
