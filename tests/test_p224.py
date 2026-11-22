"""Native ECDSA P-224 verification and the .tfw header signature check.

Where each vector comes from:
  - RFC 6979 appendix A.2.4 (P-224 key and its deterministic signatures of "sample" and "test"
    with SHA-1/224/256/384/512): published values, re-derived here from the private key and
    nonce by tests/p224_reference.py (the cryptography package) (public key = x*G, r and s recomputed) before use.
  - Edge cases in the style of Project Wycheproof's ecdsa_secp224r1_sha256 tests (r or s zero,
    n or above; keys off the curve, on another curve, at infinity, out of range; u1*G + u2*Q at
    infinity; the doubling case; e = 0; e >= n; Q = G and Q = -G; the malleable s): built here
    from first principles with the reference implementation; the expected result follows from
    the arithmetic, and the reference agrees.
  - OpenSSL (an independent implementation; skipped when the command is absent): signatures it
    makes with a fresh P-224 key must verify, and their corruptions must not.
  - Random signatures from the reference, and every single-bit corruption of one valid input.
  - The three official packages (private area; skipped without them and the vendor public key):
    their headers must verify; every single-bit change inside the signed range changes the
    message, and a fixed sample of 64 bits fails the whole check (one bit per byte: the synthetic
    header, the same check).
A test fails if the verifier accepts any invalid case.
"""

import hashlib
import random
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import p224_reference as ref
import pytest
import xyf_hosttest

from xy_forever import paths, tfw_extract

VALID, ARGUMENT, KEY, RANGE, MISMATCH = range(5)  # p224::Status
H_VALID, H_SIZE, H_KEY_INDEX, H_KEY, H_RANGE, H_MISMATCH = range(6)  # tfw::Signature

RFC_D = 0xF220266E1105BFE3083E03EC7A3A654651F45E37167E88600BF257C1
RFC_Q = (
    0x00CF08DA5AD719E42707FA431292DEA11244D64FC51610D94B130D6C,
    0xEEAB6F3DEBE455E3DBF85416F7030CBD94F34F2D6F232C69F3C1385A,
)
RFC_VECTORS = (  # (hash, message, k or None, r, s): RFC 6979 A.2.4
    (
        'sha256',
        b'sample',
        0xAD3029E0278F80643DE33917CE6908C70A8FF50A411F06E41DEDFCDC,
        0x61AA3DA010E8E8406C656BC477A7A7189895E7E840CDFE8FF42307BA,
        0xBC814050DAB5D23770879494F9E0A680DC1AF7161991BDE692B10101,
    ),
    (
        'sha256',
        b'test',
        0xFF86F57924DA248D6E44E8154EB69F0AE2AEBAEE9931D0B5A969F904,
        0xAD04DDE87B84747A243A631EA47A1BA6D1FAA059149AD2440DE6FBA6,
        0x178D49B1AE90E3D8B629BE3DB5683915F4E8C99FDF6E666CF37ADCFD,
    ),
    (
        'sha1',
        b'sample',
        None,
        0x22226F9D40A96E19C4A301CE5B74B115303C0F3A4FD30FC257FB57AC,
        0x66D1CDD83E3AF75605DD6E2FEFF196D30AA7ED7A2EDF7AF475403D69,
    ),
    (
        'sha224',
        b'sample',
        None,
        0x1CDFE6662DDE1E4A1EC4CDEDF6A1F5A2FB7FBD9145C12113E6ABFD3E,
        0xA6694FD7718A21053F225D3F46197CA699D45006C06F871808F43EBC,
    ),
    (
        'sha384',
        b'sample',
        None,
        0x0B115E5E36F0F9EC81F1325A5952878D745E19D7BB3EABFABA77E953,
        0x830F34CCDFE826CCFDC81EB4129772E20E122348A2BBD889A1B1AF1D,
    ),
    (
        'sha512',
        b'sample',
        None,
        0x074BD1D979D5F32BF958DDC61E4FB4872ADCAFEB2256497CDAC30397,
        0xA4CECA196C3D5A1FF31027B33185DC8EE43F288B21AB342E5D8EB084,
    ),
    (
        'sha224',
        b'test',
        None,
        0xC441CE8E261DED634E4CF84910E4C5D1D22C5CF3B732BB204DBEF019,
        0x902F42847A63BDC5F6046ADA114953120F99442D76510150F372A3F4,
    ),
)
OFFICIAL = paths.FIRMWARE / 'official'
PACKAGES = ('opxy_firmware_1_1_33+1.tfw', 'opxy_firmware_1_2_1.tfw', 'opxy_firmware_1_2_5.tfw')
SIGNED = [i for i in range(0x300) if not ref.SIGNED_HOLE[0] <= i < ref.SIGNED_HOLE[1]]
# 64 more (byte, bit) flips of the signed range through the whole check, beyond one bit per byte.
SAMPLED_BITS = set(random.Random(224).sample([(at, bit) for at in SIGNED for bit in range(8)], 64))


def be(value, size=28):
    return value.to_bytes(size, 'big')


def key_bytes(point):
    return be(point[0]) + be(point[1])


@pytest.mark.gate
class Native(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.native = xyf_hosttest.module('p224')

    def verify(self, key, digest, r, s):
        """Native status; key a point or 56 bytes, r and s integers or 28 bytes."""
        key = key if isinstance(key, bytes) else key_bytes(key)
        r, s = (v if isinstance(v, bytes) else be(v) for v in (r, s))
        return self.native.verify(key, digest, r, s)

    def header(self, header, key):
        return self.native.verify_header(bytes(header), len(header), key)

    def signed_digest(self, header):
        return self.native.signed_digest(bytes(header))

    def check_signed_range(self, header, key, per_byte=True):
        """Every bit outside +0x10..+0x50 changes the message; one bit per byte (when per_byte) and
        SAMPLED_BITS through the whole check fails; inside r and s every flip fails."""
        digest = self.signed_digest(header)
        header = bytearray(header)
        for at in SIGNED:
            for bit in range(8):
                header[at] ^= 1 << bit
                self.assertNotEqual(self.signed_digest(header), digest, (hex(at), bit))
                if per_byte and bit == at % 8 or (at, bit) in SAMPLED_BITS:  # +0x89: refused before hashing
                    self.assertEqual(
                        self.header(header, key), H_KEY_INDEX if at == 0x89 else H_MISMATCH, (hex(at), bit)
                    )
                header[at] ^= 1 << bit
        for at in list(range(0x14, 0x30)) + list(range(0x34, 0x50)):
            header[at] ^= 1 << (at % 8)
            self.assertNotEqual(self.header(header, key), H_VALID, hex(at))
            header[at] ^= 1 << (at % 8)


class VerifierTests(Native):
    def test_rfc6979_vectors(self):
        self.assertEqual(ref.public_key(RFC_D), RFC_Q)
        for name, message, k, r, s in RFC_VECTORS:
            digest = hashlib.new(name, message).digest()
            with self.subTest(hash=name, message=message):
                if k is not None:
                    self.assertEqual(ref.sign(RFC_D, digest, k), (r, s))
                self.assertTrue(ref.verify(RFC_Q, digest, r, s))
                self.assertEqual(self.verify(RFC_Q, digest, r, s), VALID)
                wrong = hashlib.new(name, message + b'.').digest()
                for case, args, status in (
                    ('wrong message', (wrong, r, s), MISMATCH),
                    ('wrong r', (digest, r + 1, s), MISMATCH),
                    ('wrong s', (digest, r, s + 1), MISMATCH),
                    ('r and s swapped', (digest, s, r), MISMATCH),
                    ('other key', (digest, r, s), MISMATCH),
                ):
                    key = ref.public_key(RFC_D + 1) if case == 'other key' else RFC_Q
                    self.assertEqual(self.verify(key, *args), status, case)

    def test_signature_range(self):
        digest = hashlib.sha256(b'sample').digest()
        _, _, _, r, s = RFC_VECTORS[0]
        for bad in (0, ref.N, ref.N + 1, 2**224 - 1):
            with self.subTest(bad=hex(bad)):
                self.assertEqual(self.verify(RFC_Q, digest, bad, s), RANGE)
                self.assertEqual(self.verify(RFC_Q, digest, r, bad), RANGE)
        self.assertEqual(self.verify(RFC_Q, digest, 0, 0), RANGE)
        self.assertEqual(self.verify(RFC_Q, digest, 1, 1), MISMATCH)
        self.assertEqual(self.verify(RFC_Q, digest, ref.N - 1, ref.N - 1), MISMATCH)
        # Plain ECDSA (and the stock verifier) accepts (r, n - s) as well: no low-s rule.
        self.assertTrue(ref.verify(RFC_Q, digest, r, ref.N - s))
        self.assertEqual(self.verify(RFC_Q, digest, r, ref.N - s), VALID)

    def test_public_key_validation(self):
        digest = hashlib.sha256(b'sample').digest()
        _, _, _, r, s = RFC_VECTORS[0]
        x, y = RFC_Q
        # A point on y^2 = x^3 - 3x + (b + 1): an invalid-curve key.
        other_x, other_y = 2, 0x7C824C6D6EB2BF81E21907F1D8C88D8249493C2165F2E11E3AED2695
        self.assertEqual((other_y**2 - (other_x**3 - 3 * other_x + ref.B + 1)) % ref.P, 0)
        cases = {
            'y + 1': (x, y + 1),
            'x + 1': (x + 1, y),
            'infinity (all zero)': (0, 0),
            'x = p': (ref.P, y),
            'y = p': (x, ref.P),
            'all ones': (2**224 - 1, 2**224 - 1),
            'other curve': (other_x, other_y),
        }
        for name, point in cases.items():
            with self.subTest(name):
                self.assertFalse(ref.on_curve(point))
                self.assertEqual(self.verify(key_bytes(point), digest, r, s), KEY)
        self.assertEqual(self.verify(ref.negate(RFC_Q), digest, r, s), MISMATCH)

    def test_special_keys_and_scalars(self):
        rng = random.Random(4324)
        for name, d in (('Q = G', 1), ('Q = -G', ref.N - 1), ('Q = 2G', 2)):
            with self.subTest(name):
                digest = rng.randbytes(32)
                r, s = ref.sign(d, digest, rng.randrange(1, ref.N))
                self.assertEqual(self.verify(ref.public_key(d), digest, r, s), VALID)
                self.assertEqual(self.verify(ref.public_key(d), digest, r, (s + 1) % ref.N or 1), MISMATCH)
        d, k = RFC_D, 0x1234567890ABCDEF1234567890ABCDEF1234567890ABCDEF12345678
        q = ref.public_key(d)
        r = ref.public_key(k)[0] % ref.N
        # u1*G + u2*Q = (e + r*d)/s * G is the point at infinity when e = -r*d (mod n), whatever s is.
        e = (-r * d) % ref.N
        self.assertEqual((e + r * d) % ref.N, 0)
        for s in (1, 2, ref.N - 1, rng.randrange(1, ref.N)):
            self.assertEqual(self.verify(q, be(e), r, s), MISMATCH)
        # The doubling case: e = r*d makes u1*G = u2*Q; a genuine signature, s = 2rd/k.
        e = r * d % ref.N
        s = 2 * r * d * pow(k, -1, ref.N) % ref.N
        self.assertTrue(ref.verify(q, be(e), r, s))
        self.assertEqual(self.verify(q, be(e), r, s), VALID)
        # e = 0 (u1 = 0), e >= n (reduced), a long digest (truncated) and a one-byte digest.
        for digest in (bytes(32), b'\xff' * 28, b'\xff' * 28 + b'\x12\x34\x56\x78', b'\x7f'):
            with self.subTest(digest=digest.hex()):
                r, s = ref.sign(d, digest, k)
                self.assertEqual(self.verify(q, digest, r, s), VALID)
        r, s = ref.sign(d, b'\xff' * 28, k)
        self.assertEqual(self.verify(q, be(2**224 - 1 - ref.N), r, s), VALID)  # the same e mod n
        self.assertEqual(self.verify(q, be(2**224 - 2 - ref.N), r, s), MISMATCH)

    def test_arguments(self):
        _, _, _, r, s = RFC_VECTORS[0]
        for digest in (b'', bytes(65)):
            self.assertEqual(self.verify(RFC_Q, digest, r, s), ARGUMENT)
        self.assertEqual(self.native.verify(None, bytes(32), be(r), be(s)), ARGUMENT)

    def test_every_single_bit_corruption_is_refused(self):
        name, message, _, r, s = RFC_VECTORS[0]
        inputs = [
            bytearray(key_bytes(RFC_Q)),
            bytearray(hashlib.sha256(message).digest()),
            bytearray(be(r)),
            bytearray(be(s)),
        ]
        self.assertEqual(self.native.verify(*map(bytes, inputs)), VALID)
        for which, data in enumerate(inputs):
            for bit in range(len(data) * 8):
                data[bit // 8] ^= 1 << (bit % 8)
                status = self.native.verify(*map(bytes, inputs))
                data[bit // 8] ^= 1 << (bit % 8)
                # Only the digest's leftmost 224 bits count (FIPS 186-4 6.4): its last four
                # bytes are not part of e.
                truncated = which == 1 and bit >= 224
                self.assertEqual(status == VALID, truncated, (which, bit))

    def test_random_signatures_agree_with_the_reference(self):
        rng = random.Random(4325)
        for _ in range(40):
            d = rng.randrange(1, ref.N)
            q = ref.public_key(d)
            digest = rng.randbytes(32)
            r, s = ref.sign(d, digest, rng.randrange(1, ref.N))
            self.assertEqual(self.verify(q, digest, r, s), VALID)
            garbage = (rng.randrange(1, ref.N), rng.randrange(1, ref.N))
            self.assertFalse(ref.verify(q, digest, *garbage))
            self.assertEqual(self.verify(q, digest, *garbage), MISMATCH)

    @unittest.skipUnless(shutil.which('openssl'), 'openssl absent')
    def test_openssl_signatures(self):
        with tempfile.TemporaryDirectory(prefix='xyf-p224-openssl-') as tmp:
            tmp = Path(tmp)

            def openssl(*args, data=None):
                return subprocess.run(['openssl', *map(str, args)], input=data, capture_output=True, check=True).stdout

            openssl('genpkey', '-algorithm', 'EC', '-pkeyopt', 'ec_paramgen_curve:P-224', '-out', tmp / 'k.pem')
            spki = openssl('pkey', '-in', tmp / 'k.pem', '-pubout', '-outform', 'DER')
            self.assertEqual(spki[-57], 4)  # uncompressed point, last in the SPKI
            key = spki[-56:]
            for i in range(12):
                message = b'xy-forever openssl message %d' % i
                der = openssl('dgst', '-sha256', '-sign', tmp / 'k.pem', data=message)
                r, s = der_signature(der)
                digest = hashlib.sha256(message).digest()
                self.assertEqual(self.verify(key, digest, r, s), VALID)
                self.assertEqual(self.verify(key, hashlib.sha256(message + b'!').digest(), r, s), MISMATCH)
                self.assertNotEqual(self.verify(key, digest, s, r), VALID)

    def test_speed(self):
        name, message, _, r, s = RFC_VECTORS[0]
        micros = self.native.time(key_bytes(RFC_Q), hashlib.sha256(message).digest(), be(r), be(s), 50)
        self.assertGreater(micros, 0)
        self.assertLess(micros, 50000)  # host -O2: about 1 ms


def der_signature(der):
    """(r, s) from an ECDSA-Sig-Value: SEQUENCE { INTEGER r, INTEGER s } (short lengths)."""
    assert der[0] == 0x30 and der[1] == len(der) - 2
    values, at = [], 2
    for _ in range(2):
        assert der[at] == 2
        size = der[at + 1]
        values.append(int.from_bytes(der[at + 2 : at + 2 + size], 'big'))
        at += 2 + size
    return tuple(values)


SYNTHETIC_D = int.from_bytes(hashlib.sha256(b'xy-forever synthetic header key').digest(), 'big') % ref.N


def synthetic_header(seed):
    rng = random.Random(seed)
    header = bytearray(rng.randbytes(0x300))
    header[0x10:0x50] = bytes(0x40)
    header[0x84:0x8A] = b'TESG\x01\x00'
    return ref.sign_header(bytes(header), SYNTHETIC_D, rng.randrange(1, ref.N))


class HeaderTests(Native):
    """tfw::verify_header on synthetic headers signed by the reference."""

    def test_message_construction(self):
        rng = random.Random(4326)
        for _ in range(20):
            header = rng.randbytes(0x300)
            self.assertEqual(self.signed_digest(header), ref.header_digest(header))
        header = bytearray(rng.randbytes(0x300))
        before = bytes(header)
        self.signed_digest(header)
        self.assertEqual(bytes(header), before)

    def test_synthetic_header(self):
        key = key_bytes(ref.public_key(SYNTHETIC_D))
        header = bytearray(synthetic_header(1))
        self.assertEqual(self.header(header, key), H_VALID)
        self.assertEqual(self.native.verify_header(bytes(header), 0x2FF, key), H_SIZE)
        self.assertEqual(self.native.verify_header(bytes(header) + b'\0', 0x301, key), H_SIZE)
        for index in (1, 0xFF):
            changed = bytearray(header)
            changed[0x89] = index
            self.assertEqual(self.header(changed, key), H_KEY_INDEX)
        self.assertEqual(self.header(header, key_bytes(ref.public_key(SYNTHETIC_D + 1))), H_MISMATCH)
        self.assertEqual(self.header(header, bytes(56)), H_KEY)
        swapped = bytearray(header)
        swapped[0x14:0x30], swapped[0x34:0x50] = header[0x34:0x50], header[0x14:0x30]
        self.assertNotEqual(self.header(swapped, key), H_VALID)
        # The four bytes before r and before s are zeroed in the message and read by nothing:
        # stock accepts any value there, and so does this check.
        for at in (0x10, 0x13, 0x30, 0x33):
            changed = bytearray(header)
            changed[at] ^= 0xFF
            self.assertEqual(self.header(changed, key), H_VALID)
        self.check_signed_range(header, key)


@unittest.skipUnless(
    tfw_extract.PUBLIC_KEY.is_file() and all((OFFICIAL / p).is_file() for p in PACKAGES),
    'the official packages or the vendor public key are not in the private area',
)
class OfficialHeaderTests(Native):
    """The three official headers with the vendor public key (read from the private area)."""

    def official(self):
        key = tfw_extract.public_key()
        for package in PACKAGES:
            with open(OFFICIAL / package, 'rb') as f:
                raw = f.read(0x380)
            self.assertEqual(raw[:4], b'\xba\xbe\xca\xfe')
            yield package, raw[0x80:0x380], key

    def test_each_package_verifies(self):
        for package, header, key in self.official():
            with self.subTest(package=package):
                self.assertEqual(header[0x89], 0)
                self.assertEqual(self.header(header, key), H_VALID)
                r, s = ref.header_signature(header)
                self.assertTrue(ref.verify(ref.decode_point(key), ref.header_digest(header), r, s))

    def test_any_change_in_the_signed_range_fails(self):
        for package, header, key in self.official():
            with self.subTest(package=package):  # one bit per byte: test_synthetic_header (the check's own range)
                self.check_signed_range(header, key, per_byte=False)
                swapped = bytearray(header)
                swapped[0x14:0x30], swapped[0x34:0x50] = header[0x34:0x50], header[0x14:0x30]
                self.assertNotEqual(self.header(swapped, key), H_VALID)
                changed = bytearray(header)
                changed[0x89] = 1
                self.assertEqual(self.header(changed, key), H_KEY_INDEX)


if __name__ == '__main__':
    unittest.main()
