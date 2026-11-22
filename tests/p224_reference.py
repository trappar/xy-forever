"""The ECDSA P-224 reference for the tests, on the `cryptography` package (OpenSSL).

It signs synthetic headers with synthetic keys, re-derives the RFC 6979 vectors and builds edge cases.
It is never used to decide anything about a real package: the device's own verifier (native/p224.cpp)
does that, and this file is the independent check of it. Signing takes an explicit nonce k (the RFC
vectors name theirs), so r and s are computed here from k*G as FIPS 186-4 6.4 states them.
"""

import hashlib

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, utils

CURVE = ec.SECP224R1()
# FIPS 186-4 D.1.2.2 (curve P-224); the same values sit in the 1.2.1 bootloader at 0x11b00ea4.
P = 2**224 - 2**96 + 1
B = 0xB4050A850C04B3ABF54132565044B0B7D7BFD8BA270B39432355FFB4
N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFF16A2E0B8F03E13DD29455C5C2A3D
SIZE = 28
# Prehashed digests by length: the verifier takes a digest of any of these sizes.
DIGESTS = {
    h.digest_size: h for h in (hashes.SHA1(), hashes.SHA224(), hashes.SHA256(), hashes.SHA384(), hashes.SHA512())
}


def on_curve(point):
    try:
        ec.EllipticCurvePublicNumbers(*point, CURVE).public_key()
    except ValueError:
        return False
    return True


def negate(point):
    return point[0], (-point[1]) % P


def bits2int(digest):
    """The leftmost min(len, 224) bits of the digest as an integer (FIPS 186-4 6.4)."""
    value = int.from_bytes(digest, 'big')
    excess = len(digest) * 8 - 224
    return value >> excess if excess > 0 else value


def public_key(d):
    """d*G as (x, y), 1 <= d < n."""
    numbers = ec.derive_private_key(d, CURVE).public_key().public_numbers()
    return numbers.x, numbers.y


def sign(d, digest, k):
    r = public_key(k)[0] % N
    s = pow(k, -1, N) * (bits2int(digest) + r * d) % N
    assert r and s, 'pick another k'
    return r, s


def verify(q, digest, r, s):
    """OpenSSL's ECDSA verify of (r, s) over the digest (length 20, 28, 32, 48 or 64) with key q."""
    if not (0 < r < N and 0 < s < N):
        return False
    key = ec.EllipticCurvePublicNumbers(*q, CURVE).public_key()
    try:
        key.verify(utils.encode_dss_signature(r, s), digest, ec.ECDSA(utils.Prehashed(DIGESTS[len(digest)])))
    except InvalidSignature:
        return False
    return True


def encode_point(point):
    """Qx || Qy, 28-byte big-endian each: the layout the stock verifier reads from OTP selector 9."""
    return point[0].to_bytes(SIZE, 'big') + point[1].to_bytes(SIZE, 'big')


def decode_point(data):
    return int.from_bytes(data[:SIZE], 'big'), int.from_bytes(data[SIZE : 2 * SIZE], 'big')


# The signed message of a .tfw crypto header (stock 88a17f2a): the 0x300 bytes with +0x10..+0x50
# zeroed, SHA-256; r and s are 28-byte big-endian integers at +0x14 and +0x34.
HEADER_BYTES, SIGNED_HOLE, R_AT, S_AT = 0x300, (0x10, 0x50), 0x14, 0x34


def header_digest(header):
    header = bytearray(header[:HEADER_BYTES])
    header[SIGNED_HOLE[0] : SIGNED_HOLE[1]] = bytes(SIGNED_HOLE[1] - SIGNED_HOLE[0])
    return hashlib.sha256(bytes(header)).digest()


def header_signature(header):
    return (int.from_bytes(header[R_AT : R_AT + SIZE], 'big'), int.from_bytes(header[S_AT : S_AT + SIZE], 'big'))


def sign_header(header, d, k):
    """The header with r and s written in place (the rest of +0x10..+0x50 left as it was)."""
    r, s = sign(d, header_digest(header), k)
    signed = bytearray(header)
    signed[R_AT : R_AT + SIZE] = r.to_bytes(SIZE, 'big')
    signed[S_AT : S_AT + SIZE] = s.to_bytes(SIZE, 'big')
    return bytes(signed)
