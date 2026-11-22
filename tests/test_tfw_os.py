"""OS-only .tfw extractor (native/tfw_os) on synthetic packages built here.

Packages use the public synthetic AES vector from test_tfw.py, never a device key, and are
signed (where a test needs it) with a synthetic P-224 key through tests/p224_reference.py. Every
malformed input must fail with no output file created and nothing handed to the writer.
"""

import hashlib
import json
import random
import struct
import tempfile
import unittest
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import p224_reference as ecdsa
import pytest
from test_ldr import image
from test_tfw import KEY, container

from xy_forever import hostbuild, tfw_extract
from xy_forever.xyf_entry import firmware_document

KEYS = bytes([0]) + KEY
# Synthetic signing key and nonce (derived from fixed labels; nothing to do with the vendor key).
SIGNING_KEY = int.from_bytes(hashlib.sha256(b'xy-forever synthetic tfw signing key').digest(), 'big') % ecdsa.N
NONCE = int.from_bytes(hashlib.sha256(b'xy-forever synthetic tfw nonce').digest(), 'big') % ecdsa.N
PUBLIC_KEY = ecdsa.encode_point(ecdsa.public_key(SIGNING_KEY))
MODES = ('--unverified', '--reject-signature', '--public-key-stdin')
OS = 'firmware/blackfin/bin/os.ldr'
VERSION = 'firmware/blackfin/bin/os.version'
BOOTLOADER = 'firmware/blackfin/bin/bootloader.ldr'
GOOD_OS = image(
    [(4, 0x11A00000, 4096, 0, bytes(range(256)) * 16), (4, 0x80001000, 3000, 0, b'\x5a' * 3000)], entry=0x11A00000
)
GOOD_VERSION = b'1.2.5+0@' + b'f705bf1f75' * 4


def deflate(data):
    compressor = zlib.compressobj(6, zlib.DEFLATED, -15)
    return compressor.compress(data) + compressor.flush()


def member(name, data, **fields):
    m = dict(
        name=name.encode() if isinstance(name, str) else name,
        data=data,
        method=8,
        flags=0,
        needed=20,
        made_by=0x0314,
        mode=0o100644,
        cextra=b'',
        lextra=b'',
        comment=b'',
        disk=0,
    )
    m['compressed'] = deflate(data)
    m.update(fields)
    for key in ('crc', 'size', 'csize'):
        m.setdefault(key, None)
    return m


def standard(
    order=(
        'firmware/firmware.version',
        BOOTLOADER,
        OS,
        VERSION,
        'firmware/blackfin/bin/bootloader.version',
        'firmware/blackfin/content/a.wav',
    ),
):
    rng = random.Random(4301)
    data = {
        OS: GOOD_OS,
        VERSION: GOOD_VERSION,
        BOOTLOADER: rng.randbytes(20000),
        'firmware/firmware.version': b'1.2.5+0\n',
        'firmware/blackfin/bin/bootloader.version': b'1000.0.23+0\n',
        'firmware/blackfin/content/a.wav': rng.randbytes(5000),
    }
    return [member(name, data[name]) for name in order]


def build_zip(members, *, comment=b'', eocd=None, gap_after=None):
    """Returns (zip bytes, {name: (local offset, local end)}, central directory offset)."""
    body, central, spans = b'', b'', {}
    for i, m in enumerate(members):
        data, compressed = m['data'], m['compressed']
        crc = zlib.crc32(data) if m['crc'] is None else m['crc']
        size = len(data) if m['size'] is None else m['size']
        csize = len(compressed) if m['csize'] is None else m['csize']
        local_name = m.get('local_name', m['name'])
        offset = len(body)
        local = (
            struct.pack(
                '<I5H3I2H',
                0x04034B50,
                m['needed'],
                m['flags'],
                m['method'],
                0,
                0,
                crc,
                csize,
                size,
                len(local_name),
                len(m['lextra']),
            )
            + local_name
            + m['lextra']
        )
        body += local + compressed
        spans[m['name'].decode('latin-1')] = (offset, len(body))
        if gap_after == i:
            body += b'\0' * 7
        central += (
            struct.pack(
                '<I6H3I5H2I',
                0x02014B50,
                m['made_by'],
                m['needed'],
                m['flags'],
                m['method'],
                0,
                0,
                crc,
                csize,
                size,
                len(m['name']),
                len(m['cextra']),
                len(m['comment']),
                m['disk'],
                0,
                m['mode'] << 16,
                m.get('offset', offset),
            )
            + m['name']
            + m['cextra']
            + m['comment']
        )
    fields = dict(disk=0, start=0, here=len(members), total=len(members), size=len(central), offset=len(body))
    fields.update(eocd or {})
    end = (
        struct.pack(
            '<I4H2IH',
            0x06054B50,
            fields['disk'],
            fields['start'],
            fields['here'],
            fields['total'],
            fields['size'],
            fields['offset'],
            len(comment),
        )
        + comment
    )
    return body + central + end, spans, len(body)


@pytest.mark.gate
class Extractor(unittest.TestCase):
    """Builds the host extractor once per class (the same build xyf-package uses)."""

    flags = ('-O2',)

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='xyf-tfw-os-')
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.root = Path(cls.tmp.name)
        cls.binary = tfw_extract.build(cls.root, cls.flags, run=hostbuild.run)

    def extract(self, package, *args, keys=KEYS, public_key=None, name='package'):
        """Unsigned synthetic packages run --unverified unless a test names a signature mode
        (name: the run's own input and output files, for runs at the same time)."""
        path, out = self.root / (name + '.tfw'), self.root / (name + '.os.ldr')
        path.write_bytes(package)
        if out.exists():
            out.unlink()
        if public_key is None and not set(args) & set(MODES):
            args += ('--unverified',)
        report = tfw_extract.run(self.binary, 'extract', path, out, *args, keys=keys, public_key=public_key)
        report['output'] = out.read_bytes() if out.exists() else None
        return report

    def refuse(self, package, status, *args, keys=KEYS, detail=None, public_key=None):
        report = self.extract(package, *args, keys=keys, public_key=public_key)
        self.assertEqual(report['status'], status, report)
        if detail:
            self.assertEqual({k: report[k] for k in detail}, detail, report)
        self.assertEqual((report['written'], report['output_created'], report['output']), (0, False, None))
        self.assertEqual((report['bytes'], report['crc32'], report['sha256']), (0, '00000000', '0' * 64))
        return report


def package(members=None, **options):
    payload, spans, directory = build_zip(standard() if members is None else members, **options)
    return container(payload)


def signed(data, d=SIGNING_KEY, k=NONCE):
    """The package with its crypto header (at 0x80) signed as stock expects."""
    return data[:0x80] + ecdsa.sign_header(data[0x80:0x380], d, k) + data[0x380:]


def replace(members, name, **fields):
    if 'data' in fields and 'compressed' not in fields:
        fields['compressed'] = deflate(fields['data'])
    return [dict(m, **fields) if m['name'] == name.encode() else m for m in members]


class ExtractorTests(Extractor):
    def test_valid_package_gives_the_image_and_its_facts(self):
        report = self.extract(package())
        self.assertEqual(report['status'], 'ok', report)
        self.assertEqual(report['output'], GOOD_OS)
        self.assertEqual(
            (report['bytes'], report['crc32'], report['sha256']),
            (len(GOOD_OS), '%08x' % zlib.crc32(GOOD_OS), hashlib.sha256(GOOD_OS).hexdigest()),
        )
        self.assertEqual(
            (report['version'], report['package_version'], report['entry']), ('1.2.5+0', '1.2.1+0', 0x11A00000)
        )
        self.assertEqual((report['written'], report['key_calls'], report['records']), (len(GOOD_OS), 1, 4))
        self.assertLess(report['scratch_bytes'] + report['workspace_bytes'], 4 * 1024 * 1024)

    def test_digest_command_hashes_the_ciphertext_without_a_key(self):
        data = package()
        path = self.root / 'digest.tfw'
        path.write_bytes(data)
        report = tfw_extract.run(self.binary, 'digest', path)
        self.assertEqual(report['ciphertext_sha256'], hashlib.sha256(data[0x380:]).hexdigest())

    def allowed(self, data, spans, directory):
        """Header + name block, the directory to the end, and both wanted members: each range
        widened to whole cipher blocks plus the preceding block (the CBC chaining value)."""
        base = 0x380 + 256

        def blocks(lo, hi):
            return base + (lo & ~15) - 16, base + ((hi + 15) & ~15)

        return [(0, base)] + [blocks(*spans[n]) for n in (OS, VERSION)] + [(blocks(directory, 0)[0], len(data))]

    def test_only_header_directory_and_wanted_members_are_read(self):
        orders = [
            ('firmware/firmware.version', BOOTLOADER, OS, VERSION, 'firmware/blackfin/content/a.wav'),
            (OS, BOOTLOADER, VERSION),
            (VERSION, OS, BOOTLOADER),
            (BOOTLOADER, VERSION, 'firmware/firmware.version', OS),
        ]
        for order in orders:
            with self.subTest(order=order):
                payload, spans, directory = build_zip(standard(order))
                data = container(payload)
                ranges = self.allowed(data, spans, directory)
                # The bootloader member lies outside every allowed range, apart from the CBC
                # blocks it shares with a neighbour (at most 31 bytes at each end).
                lo, hi = spans[BOOTLOADER]
                inner = (0x480 + lo + 32, 0x480 + hi - 32)
                self.assertFalse([r for r in ranges if r[0] < inner[1] and inner[0] < r[1]], ranges)
                report = self.extract(data, *[a for r in ranges for a in ('--allow', '%d:%d' % r)])
                self.assertEqual((report['status'], report['refused_reads']), ('ok', 0), report)
                self.assertEqual(report['output'], GOOD_OS)
                # Control: the same restriction without the image's range does refuse.
                report = self.extract(data, *[a for r in ranges[:1] + ranges[2:] for a in ('--allow', '%d:%d' % r)])
                self.assertEqual(report['status'], 'io')
                self.assertGreater(report['refused_reads'], 0)
                self.assertIsNone(report['output'])

    def test_signature_seam_runs_before_the_key_and_the_hash(self):
        report = self.refuse(package(), 'signature', '--reject-signature')
        self.assertEqual(report['key_calls'], 0)
        report = self.refuse(package(), 'ciphertext_hash', '--digest', '00' * 32)
        self.assertEqual(report['key_calls'], 0)

    def test_signed_package_verifies_before_the_key_is_requested(self):
        good = signed(package())
        report = self.extract(good, public_key=PUBLIC_KEY)
        self.assertEqual((report['status'], report['signature'], report['output']), ('ok', 'verified', GOOD_OS))
        self.assertEqual(self.extract(good)['signature'], 'not checked')
        other = ecdsa.encode_point(ecdsa.public_key(SIGNING_KEY + 1))
        cases = {
            'unsigned (r = s = 0)': (package(), PUBLIC_KEY, 'range'),
            'other key': (good, other, 'mismatch'),
            'key off the curve': (good, PUBLIC_KEY[:-1] + bytes([PUBLIC_KEY[-1] ^ 1]), 'public_key'),
        }
        for name, at in (
            ('version', 0x80 + 0x90),
            ('ciphertext hash', 0x80 + 0x9C),
            ('IV', 0x80 + 0x70),
            ('r', 0x80 + 0x14 + 27),
            ('s', 0x80 + 0x34),
        ):
            changed = bytearray(good)
            changed[at] ^= 1
            cases[name] = (bytes(changed), PUBLIC_KEY, 'mismatch')
        swapped = bytearray(good)
        swapped[0x94:0xB0], swapped[0xB4:0xD0] = good[0xB4:0xD0], good[0x94:0xB0]
        cases['r and s swapped'] = (bytes(swapped), PUBLIC_KEY, 'mismatch')
        for name, (data, key, detail) in cases.items():
            with self.subTest(name):
                report = self.refuse(
                    data, 'signature', public_key=key, detail={'container': 'signature', 'signature': detail}
                )
                self.assertEqual(report['key_calls'], 0)
        # The ciphertext is bound through the signed hash: a flipped ciphertext bit passes the
        # signature and fails the hash comparison, still before the key is requested.
        changed = bytearray(good)
        changed[-20] ^= 1
        report = self.refuse(bytes(changed), 'ciphertext_hash', public_key=PUBLIC_KEY)
        self.assertEqual((report['signature'], report['key_calls']), ('verified', 0))

    def test_signature_mode_is_required(self):
        path = self.root / 'mode.tfw'
        path.write_bytes(package())
        report = tfw_extract.run(self.binary, 'extract', path, self.root / 'mode.ldr', keys=KEYS)
        self.assertEqual(report['status'], 'usage')
        self.assertFalse((self.root / 'mode.ldr').exists())

    def test_keys(self):
        self.assertEqual(self.refuse(package(), 'key', keys=b'')['key_calls'], 1)
        self.refuse(package(), 'key', keys=bytes([1]) + KEY)
        wrong = self.refuse(package(), 'container', keys=bytes(33))
        self.assertIn(wrong['container'], ('padding', 'filename'))
        clear = container(build_zip(standard())[0], index=0xFE)
        self.assertEqual(self.refuse(clear, 'key')['key_calls'], 0)

    def test_container_damage(self):
        good = package()
        cases = {
            'truncated end': good[:-1],
            'truncated header': good[:0x300],
            'one block short': good[:-16],
            'empty': b'',
        }
        for name, at, value in (
            ('outer magic', 0, 0),
            ('preamble magic', 0x40, 0),
            ('TESG', 0x104, ord('X')),
            ('format', 0x108, 2),
            ('signature index', 0x109, 1),
            ('sku', 0x10C, 0),
            ('zip length', 0x100, 0),
            ('ciphertext length', 0x118, 0),
        ):
            changed = bytearray(good)
            changed[at] ^= value or 0xFF
            cases[name] = bytes(changed)
        changed = bytearray(good)
        changed[-20] ^= 1
        cases['ciphertext byte'] = bytes(changed)
        for name, data in cases.items():
            with self.subTest(name):
                report = self.extract(data)
                self.assertIn(report['status'], ('container', 'ciphertext_hash', 'usage', 'io'), report)
                if report['status'] != 'usage':
                    self.assertEqual((report['written'], report['output']), (0, None))

    def test_directory_and_layout_damage(self):
        members = standard()
        payload, spans, directory = build_zip(members)
        cases = [
            ('directory', dict(eocd=dict(offset=directory + 1))),
            ('directory', dict(eocd=dict(size=1))),
            ('directory', dict(eocd=dict(offset=len(payload)))),
            ('directory', dict(eocd=dict(here=len(members) - 1, total=len(members) - 1))),
            ('unsupported', dict(eocd=dict(here=len(members) + 1))),
            ('unsupported', dict(eocd=dict(disk=1))),
            ('unsupported', dict(eocd=dict(total=0xFFFF, here=0xFFFF))),
            ('unsupported', dict(eocd=dict(offset=0xFFFFFFFF))),
            ('directory', dict(comment=b'archive comment')),
            ('layout', dict(gap_after=1)),
        ]
        for status, options in cases:
            with self.subTest(status=status, options=options):
                self.refuse(container(build_zip(members, **options)[0]), status)
        overlap = replace(members, VERSION, offset=spans[OS][0] + 30)
        past_end = replace(members, 'firmware/blackfin/content/a.wav', csize=len(members[-1]['compressed']) + 99)
        shifted = replace(members, OS, offset=spans[OS][0] + 1)
        for name, rows in (('overlap', overlap), ('past end', past_end), ('shifted', shifted)):
            with self.subTest(name):
                self.refuse(container(build_zip(rows)[0]), 'layout')

    def test_limits(self):
        data = package()
        self.refuse(data, 'capacity', '--image-limit', str((len(GOOD_OS) - 1) & ~3))
        self.refuse(data, 'capacity', '--directory-limit', '100')
        self.refuse(data, 'capacity', '--entries', '5')
        self.refuse(data, 'argument', '--image-limit', str(64 << 20))
        self.refuse(data, 'argument', '--entries', '0')
        self.refuse(package(replace(standard(), VERSION, data=b'1.2.5+0' + b' ' * 200)), 'version')

    def test_members_that_lie_about_their_content(self):
        members = standard()
        cases = [
            ('checksum', replace(members, OS, crc=zlib.crc32(GOOD_OS) ^ 1)),
            ('checksum', replace(members, VERSION, crc=0)),
            ('member', replace(members, OS, size=len(GOOD_OS) - 1)),  # inflates to more than announced
            ('member', replace(members, OS, size=len(GOOD_OS) + 1)),  # to less
            ('member', replace(members, OS, compressed=deflate(GOOD_OS) + b'junk')),  # trailing bytes
            ('member', replace(members, OS, compressed=deflate(GOOD_OS)[:-3])),
            ('member', replace(members, OS, compressed=b'\xff' * 50)),
            ('layout', replace(members, OS, local_name=b'firmware/blackfin/bin/os.ldx')),
            ('layout', replace(members, OS, lextra=b'\x55\x54\x01\x00\x00')),
            ('version', replace(members, VERSION, data=b'1.2.five')),
            ('image', replace(members, OS, data=b'not a loader stream at all' * 4)),
            ('image', replace(members, OS, data=b'tiny')),
            ('image', replace(members, OS, data=image([(4, 0x11A00000, 4, 0, b'abcd')], entry=0x11A00002))),
            (
                'image',
                replace(
                    members,
                    OS,
                    data=image([(4, 0x11803000, 4, 0, b'abcd'), (4, 0x11A00000, 4, 0, b'abcd')], entry=0x11A00000),
                ),
            ),
        ]
        for status, rows in cases:
            with self.subTest(status=status):
                self.refuse(container(build_zip(rows)[0]), status)

    def test_zip_features_the_stock_packages_do_not_use(self):
        members = standard()
        for field, value in (
            ('flags', 8),
            ('flags', 1),
            ('flags', 0x800),
            ('flags', 2),
            ('method', 0),
            ('method', 12),
            ('needed', 45),
            ('cextra', b'\x01\x00\x00\x00'),
            ('cextra', b'\x55\x54\x01\x00\x00'),
            ('comment', b'note'),
            ('disk', 1),
            ('mode', 0o120777),
            ('csize', 0xFFFFFFFF),
            ('size', 0xFFFFFFFF),
            ('offset', 0xFFFFFFFF),
        ):
            for name in (OS, 'firmware/blackfin/content/a.wav'):
                with self.subTest(field=field, value=value, member=name):
                    rows = replace(members, name, **{field: value})
                    if (field, value) == ('method', 0):  # stored: the data as it is
                        rows = [dict(m, compressed=m['data']) if m['name'] == name.encode() else m for m in rows]
                    self.refuse(container(build_zip(rows)[0]), 'unsupported')

    def test_names(self):
        base = standard()
        decoys = [
            'firmware/blackfin/content/os.ldr',
            'firmware/blackfin/content/OS.LDR',
            'firmware/ble/bin/os.version',
            'firmware/blackfin/content/sub/Os.Version',
        ]
        tricks = [
            '../firmware/blackfin/bin/os.ldr',
            '/firmware/blackfin/bin/os.ldr',
            'firmware//blackfin/bin/os.ldr',
            'firmware/blackfin/./bin/os.ldr',
            'firmware\\blackfin\\bin\\os.ldr',
            'C:/firmware/blackfin/bin/os.ldr',
            'firmware/blackfin/bin/os.ldr.',
            'firmware/blackfin/bin/os.ldr ',
            'firmware/blackfin/content/a\x00.wav',
            'firmware/blackfin/content/caf\xe9.wav',
            'firmware/blackfin/content/',
            'firmware/unknown.bin',
            'FIRMWARE/blackfin/bin/os.ldr',
            'firmware/blackfin/content/' + 'x' * 240,
        ]
        for name in decoys + tricks:
            with self.subTest(name=name):
                rows = base + [member(name.encode('latin-1'), GOOD_OS)]
                self.refuse(container(build_zip(rows)[0]), 'name')
        for rows in (
            base + [member(OS, GOOD_OS)],
            base + [member(VERSION, GOOD_VERSION)],
            base + [member('firmware/blackfin/content/A.WAV', b'x')],
            base + [member('firmware/blackfin/content/a.wav', b'x')],
        ):
            with self.subTest(duplicate=rows[-1]['name']):
                self.refuse(container(build_zip(rows)[0]), 'duplicate')
        for missing in (OS, VERSION):
            with self.subTest(missing=missing):
                self.refuse(container(build_zip([m for m in base if m['name'] != missing.encode()])[0]), 'missing')

    def test_writer_failure_is_the_only_late_failure(self):
        report = self.extract(package(), '--fail-write')
        self.assertEqual((report['status'], report['written'], report['output_created']), ('write', 0, False))

    def test_host_entry_writes_os_ldr_and_native_manifest(self):
        keys = self.root / 'synthetic-keys.json'
        keys.write_text(json.dumps({'keys': [{'index': 0, 'read_ok': True, 'hex': KEY.hex()}]}))
        vendor = self.root / 'synthetic-public-key.json'
        vendor.write_text(json.dumps({'curve': 'P-224', 'hex': PUBLIC_KEY.hex()}))
        source = self.root / 'entry.tfw'
        source.write_bytes(signed(package()))
        out = self.root / 'entry'
        options = dict(keys=keys, public_key_file=vendor, binary=self.binary)
        report = tfw_extract.extract_os(source, out, **options)
        self.assertEqual(sorted(p.name for p in out.iterdir()), ['manifest.json', 'os.ldr'])
        self.assertEqual((out / 'os.ldr').read_bytes(), GOOD_OS)
        self.assertEqual(
            (out / 'manifest.json').read_bytes(), firmware_document('OP-XY 1.2.5', '1.2.5+0', 'official', GOOD_OS)
        )
        self.assertEqual((report['status'], report['signature']), ('ok', 'verified'))
        with self.assertRaisesRegex(tfw_extract.ExtractError, 'already exists'):
            tfw_extract.extract_os(source, out, **options)
        bad = self.root / 'bad.tfw'
        bad.write_bytes(signed(package(replace(standard(), OS, crc=1))))
        with self.assertRaisesRegex(tfw_extract.ExtractError, 'checksum'):
            tfw_extract.extract_os(bad, self.root / 'bad', **options)
        self.assertFalse((self.root / 'bad').exists())
        unsigned = self.root / 'unsigned.tfw'
        unsigned.write_bytes(package())
        with self.assertRaisesRegex(tfw_extract.ExtractError, 'signature=range'):
            tfw_extract.extract_os(unsigned, self.root / 'unsigned', **options)
        self.assertFalse((self.root / 'unsigned').exists())
        for path in (self.root / 'absent.json', source):
            with self.subTest(keys=path.name), self.assertRaisesRegex(tfw_extract.ExtractError, 'no .tfw key'):
                tfw_extract.extract_os(
                    source, self.root / 'nokey', keys=path, public_key_file=vendor, binary=self.binary
                )
            self.assertFalse((self.root / 'nokey').exists())

    def test_without_the_public_key_the_signature_is_required_unless_waived(self):
        keys = self.root / 'waive-keys.json'
        keys.write_text(json.dumps({'keys': [{'index': 0, 'read_ok': True, 'hex': KEY.hex()}]}))
        source = self.root / 'waive.tfw'
        source.write_bytes(package())
        absent = self.root / 'absent-public-key.json'
        for path in (absent, keys):
            with (
                self.subTest(public_key=path.name),
                self.assertRaisesRegex(tfw_extract.ExtractError, 'no .tfw public key'),
            ):
                tfw_extract.extract_os(source, self.root / 'waive', keys=keys, public_key_file=path, binary=self.binary)
            self.assertFalse((self.root / 'waive').exists())
        report = tfw_extract.extract_os(
            source, self.root / 'waive', keys=keys, public_key_file=absent, require_signature=False, binary=self.binary
        )
        self.assertEqual(report['signature'], 'not checked')
        self.assertEqual(
            (self.root / 'waive' / 'manifest.json').read_bytes(),
            firmware_document('OP-XY 1.2.5', '1.2.5+0', 'custom', GOOD_OS),
        )


class SanitizedDamageTests(Extractor):
    """Random damage to the clear ZIP (re-encrypted, so it gets past the container checks)."""

    flags = ('-O1', '-g', '-fsanitize=address,undefined', '-fno-omit-frame-pointer', '-fno-sanitize-recover=all')

    def test_random_damage_fails_cleanly_or_changes_nothing(self):
        payload, spans, directory = build_zip(standard())
        rng = random.Random(4302)
        hot = (
            [spans[OS][0] + i for i in range(40)]
            + [spans[VERSION][0] + i for i in range(40)]
            + list(range(directory, len(payload)))
        )
        damaged = []
        for iteration in range(120):
            data = bytearray(payload)
            for _ in range(1 + iteration % 3):
                at = rng.choice(hot) if iteration % 4 else rng.randrange(len(data))
                data[at] ^= rng.randrange(1, 256)
            damaged.append(container(bytes(data)))
        with ThreadPoolExecutor(max_workers=6) as pool:  # independent runs, each its own files
            reports = list(pool.map(lambda n: self.extract(damaged[n], name='damaged-%d' % n), range(len(damaged))))
        for report in reports:
            self.assertNotEqual(report['status'], 'usage')
            if report['status'] == 'ok':
                self.assertEqual(report['output'], GOOD_OS)
            else:
                self.assertEqual((report['written'], report['output']), (0, None), report)


if __name__ == '__main__':
    unittest.main()
