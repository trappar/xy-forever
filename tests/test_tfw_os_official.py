"""The OS-only extractor on the three official .tfw packages (private area; skipped without them).

Reads the keys (the AES key and the vendor public key) and packages from the private area at
run time; they are never copied, printed or written. Without the public key the extraction
tests run --unverified and the signature tests skip. Output goes to a temporary folder under the private area, then removed.
The reference copies are the already-decrypted packages; only their central directories and
their os.ldr / os.version files are read here, never a bootloader member.
"""

import hashlib
import tempfile
import unittest
import zipfile
from pathlib import Path

import pytest

from xy_forever import hostbuild, paths, tfw_extract
from xy_forever.xyf_entry import firmware_document

OFFICIAL = paths.FIRMWARE / 'official'
PACKAGES = (  # package, reference ZIP (directory only), reference os.ldr / os.version folder, firmware label
    (
        'opxy_firmware_1_1_33+1.tfw',
        'opxy_firmware_1_1_33/firmware_secure.zip',
        'opxy_firmware_1_1_33/extracted/firmware/blackfin/bin',
        '1.1.33+1',
    ),
    (
        'opxy_firmware_1_2_1.tfw',
        'opxy_firmware_1_2_1/firmware_secure.zip',
        'opxy_firmware_1_2_1/extracted/firmware/blackfin/bin',
        '1.2.1+0',
    ),
    (
        'opxy_firmware_1_2_5.tfw',
        'opxy_firmware_1_2_5/opxy_firmware_1_2_5.zip',
        'opxy_firmware_1_2_5/firmware/blackfin/bin',
        '1.2.5+0',
    ),
)
BASE = 0x380 + 256  # file offset of the ZIP's first byte (crypto header + encrypted name)


def vendor_key():
    return tfw_extract.public_key() if tfw_extract.PUBLIC_KEY.is_file() else None


def available():
    return tfw_extract.KEYS.is_file() and all(
        (OFFICIAL / p).is_file() and (paths.DECRYPTED / z).is_file() and (paths.DECRYPTED / r / 'os.ldr').is_file()
        for p, z, r, _ in PACKAGES
    )


@pytest.mark.gate
@unittest.skipUnless(available(), 'official packages, references or the .tfw key are not in the private area')
class OfficialPackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='xyf-tfw-os-official-')
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.binary = tfw_extract.build(cls.tmp.name, run=hostbuild.run)
        paths.SESSIONS.mkdir(parents=True, exist_ok=True)

    def test_each_package_gives_the_reference_os_ldr(self):
        for package, _, reference, label in PACKAGES:
            with (
                self.subTest(package=package),
                tempfile.TemporaryDirectory(prefix='tfw-os-', dir=paths.SESSIONS) as out,
            ):
                expected = (paths.DECRYPTED / reference / 'os.ldr').read_bytes()
                version = (paths.DECRYPTED / reference / 'os.version').read_text().strip().split('@')[0]
                folder = Path(out) / 'entry'
                report = tfw_extract.extract_os(
                    OFFICIAL / package, folder, binary=self.binary, require_signature=vendor_key() is not None
                )
                self.assertEqual((folder / 'os.ldr').read_bytes(), expected)
                self.assertEqual((report['version'], report['package_version']), (version, label))
                self.assertEqual(
                    (report['bytes'], report['sha256']), (len(expected), hashlib.sha256(expected).hexdigest())
                )
                self.assertEqual(
                    (folder / 'manifest.json').read_bytes(),
                    firmware_document('OP-XY ' + version.split('+')[0], version, 'official', expected),
                )
                self.assertEqual(report['entry'], 0x11A00000)
                self.assertEqual(report['signature'], 'verified' if vendor_key() else 'not checked')

    def test_reads_stay_off_the_bootloader_member(self):
        for package, archive, reference, _ in PACKAGES:
            with self.subTest(package=package), zipfile.ZipFile(paths.DECRYPTED / archive) as zf:
                spans = {
                    i.filename: (i.header_offset, i.header_offset + 30 + len(i.filename.encode()) + i.compress_size)
                    for i in zf.infolist()
                }

                def blocks(lo, hi):
                    return BASE + (lo & ~15) - 16, BASE + ((hi + 15) & ~15)

                size = (OFFICIAL / package).stat().st_size
                allowed = [
                    (0, BASE),
                    blocks(*spans['firmware/blackfin/bin/os.ldr']),
                    blocks(*spans['firmware/blackfin/bin/os.version']),
                    (blocks(zf.start_dir, 0)[0], size),
                ]
                for name, (lo, hi) in spans.items():
                    if 'bootloader' in name and name.endswith('.ldr'):
                        hit = [r for r in allowed if r[0] < BASE + hi and BASE + lo < r[1]]
                        self.assertEqual(hit, [], name)
                digest = tfw_extract.run(self.binary, 'digest', OFFICIAL / package)['ciphertext_sha256']
                with tempfile.TemporaryDirectory(prefix='tfw-os-', dir=paths.SESSIONS) as out:
                    report = tfw_extract.run(
                        self.binary,
                        'extract',
                        OFFICIAL / package,
                        Path(out) / 'os.ldr',
                        '--digest',
                        digest,
                        *[a for r in allowed for a in ('--allow', '%d:%d' % r)],
                        *(() if vendor_key() else ('--unverified',)),
                        keys=tfw_extract.key_records(),
                        public_key=vendor_key(),
                    )
                    self.assertEqual((report['status'], report['refused_reads']), ('ok', 0), report['status'])
                    self.assertEqual(
                        (Path(out) / 'os.ldr').read_bytes(), (paths.DECRYPTED / reference / 'os.ldr').read_bytes()
                    )


@unittest.skipUnless(
    available() and tfw_extract.PUBLIC_KEY.is_file(),
    'official packages, the .tfw key or the vendor public key are not in the private area',
)
@pytest.mark.gate
class OfficialSignatureTests(unittest.TestCase):
    """The ciphertext is bound through the signed header hash; header-only
    signature cases are in test_p224.py."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='xyf-tfw-sig-official-', dir=paths.SESSIONS)
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.binary = tfw_extract.build(cls.tmp.name, run=hostbuild.run)

    def test_a_flipped_ciphertext_bit_fails_through_the_signed_hash(self):
        package = PACKAGES[2][0]
        data = bytearray((OFFICIAL / package).read_bytes())
        copy = Path(self.tmp.name) / 'flipped.tfw'
        data[len(data) // 2] ^= 0x10
        copy.write_bytes(data)
        del data
        report = tfw_extract.run(
            self.binary,
            'extract',
            copy,
            Path(self.tmp.name) / 'os.ldr',
            keys=tfw_extract.key_records(),
            public_key=vendor_key(),
        )
        self.assertEqual(
            (report['status'], report['container'], report['signature'], report['key_calls'], report['output_created']),
            ('ciphertext_hash', 'ciphertext_hash', 'verified', 0, False),
        )
        copy.unlink()

    def test_another_public_key_refuses_every_package(self):
        key = bytearray(vendor_key())
        key[27] ^= 1  # Qx changed: off the curve
        for package, *_ in PACKAGES:
            with self.subTest(package=package):
                report = tfw_extract.run(
                    self.binary,
                    'extract',
                    OFFICIAL / package,
                    Path(self.tmp.name) / 'os.ldr',
                    keys=tfw_extract.key_records(),
                    public_key=bytes(key),
                )
                self.assertEqual(
                    (report['status'], report['signature'], report['key_calls']), ('signature', 'public_key', 0)
                )


if __name__ == '__main__':
    unittest.main()
