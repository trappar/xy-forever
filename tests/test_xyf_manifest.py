"""Package manifests (docs/storage/packages.md, docs/STORAGE.md): the strict
info.txt reader, the files.sha256 reader, the recogniser and the dependency rules, as host-built
device C++ (target/xyf_manifest.hpp), compared with the host twin in xy_forever/xyf_entry.py."""

import hashlib
import random
import shutil
import subprocess
import tempfile
import types
import unittest
import zlib
from pathlib import Path

from xy_forever import paths, sim
from xy_forever import xyf_entry as E

ROOT = Path(__file__).resolve().parents[1]

DATA = ROOT / 'tests/manifests'
KEYS = E.MANIFEST_KEYS
STATUS = E.MANIFEST_STATUS
TYPES = (None, 'firmware', 'mod-base', 'mod', 'companion')
KINDS = (None, 'official', 'open-xy', 'custom')
MACHINES = (None,) + E.MACHINES
FILE_KINDS = (
    'unknown',
    'firmware_entry',
    'assembled_entry',
    'official_tfw',
    'ldr_image',
    'xyf_stage2',
    'zip',
    'mod_base',
    'mod',
    'content_pack',
    'companion',
    'bootloader',
)
REASONS = (
    'ok',
    'bad_name',
    'short',
    'unknown',
    'refused',
    'host_only',
    'need_manifest',
    'manifest',
    'missing_image',
    'reserved_9',
    'content_set',
    'ambiguous',
)
DEPENDENCY = (
    'ok',
    'invalid',
    'not_firmware',
    'not_base',
    'not_mod',
    'not_dependent',
    'no_sha256',
    'os_mismatch',
    'generation',
    'profile',
)
TEXT = ('name', 'version', 'content', 'generation', 'profile', 'source', 'licence')


def fields(m):
    """A parsed Manifest as parse_manifest's dict."""
    out = {}
    for i, key in enumerate(KEYS):
        if not m.keys >> i & 1:
            continue
        if key == 'type':
            out[key] = TYPES[m.type]
        elif key == 'kind':
            out[key] = KINDS[m.kind]
        elif key == 'machine':
            out[key] = MACHINES[m.machine]
        elif key in ('bytes', 'machines'):
            out[key] = getattr(m, key)
        elif key == 'crc32':
            out[key] = '%08x' % m.crc32
        elif key in ('sha256', 'needs_os'):
            out[key] = getattr(m, key).hex()
        else:
            out[key] = getattr(m, key).decode()
    return out


def tfw_header(base=0x80, tesg=b'TESG', words=(1, 1)):
    head = bytearray(base + 0x300)
    if base == 0x80:
        head[0:4], head[0x40:0x44] = b'\xba\xbe\xca\xfe', b'\xbe\xef\xca\xfe'
    else:
        head[0:4] = b'\xbe\xef\xca\xfe'
    head[base + 0x84 : base + 0x88] = tesg
    head[base + 0x88 : base + 0x8A] = words[0].to_bytes(2, 'little')
    head[base + 0x8A : base + 0x8C] = words[1].to_bytes(2, 'little')
    return bytes(head)


def ldr_first(size, flags=0x5004):
    block = bytearray(
        flags.to_bytes(2, 'little') + b'\x00\xad' + bytes(4) + bytes(4) + (size - 16).to_bytes(4, 'little')
    )
    block[2] = 0
    x = 0
    for b in block:
        x ^= b
    block[2] = x  # the block's XOR checksum byte
    return bytes(block)


class ManifestTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import xyf_hosttest  # here, not at the top: web tests import this file's helpers without hosttest

        cls.lib = xyf_hosttest.module('manifest')

    # -- helpers ------------------------------------------------------------------------------
    def parse(self, text):
        status, m = self.lib.parse(text)
        self.assertEqual(status, m.status)
        return m

    def outcome(self, text):
        """(status, line, key, fields) from the device reader, checked against the host twin."""
        m = self.parse(text)
        status, key = STATUS[m.status], (KEYS[m.key] if m.key < len(KEYS) else None)
        got = (status, m.line, key, fields(m) if status == 'ok' else {})
        try:
            twin = ('ok', 0, None, E.parse_manifest(text))
        except E.ManifestError as error:
            twin = (error.status, error.line, error.key, {})
        self.assertEqual(got, twin, text)
        if status != 'ok':
            self.assertEqual(m.keys, 0)
            rest = m.after_error_fields()
            self.assertEqual(rest, bytes(len(rest)), 'fields not cleared on error')
        return got

    def legacy_same(self, text):
        status, strict, legacy, fields = self.lib.legacy(text)
        self.assertEqual(STATUS[status], 'ok', text)
        self.assertEqual(strict, legacy, text)
        return types.SimpleNamespace(**fields)  # xyf_store::Info's fields

    # -- info.txt ------------------------------------------------------------------------------
    def test_struct_layouts_match(self):
        self.assertLessEqual(self.lib.sizes(0), 512)  # Manifest
        self.assertLessEqual(self.lib.sizes(1), 512)  # ListReader

    def test_every_kind_example(self):
        expect = {
            'firmware-official.txt': dict(
                type='firmware',
                name='OP-XY 1.2.5',
                version='1.2.5',
                kind='official',
                bytes=2366912,
                crc32='9f1c0a42',
                content='1.2.5',
            ),
            'firmware-stock-121.txt': dict(type='firmware', kind='official', content='1.2.1'),
            'mod-fm2op.txt': dict(
                type='mod', name='FM2OP', version='v0.3.0', machine='synth', generation='kit-0.0.6', licence='MIT'
            ),
            'mod-base.txt': dict(type='mod-base', profile='opxy-1.2.1', generation='kit-0.0.6'),
            'assembled.txt': dict(type='firmware', generation='kit-0.0.6', profile='opxy-1.2.1'),
        }
        for name, want in expect.items():
            with self.subTest(name=name):
                status, _, _, got = self.outcome((DATA / name).read_bytes())
                self.assertEqual(status, 'ok')
                self.assertEqual({k: got[k] for k in want}, want)
        m = self.parse((DATA / 'companion.txt').read_bytes())
        self.assertEqual(
            (STATUS[m.status], TYPES[m.type], m.line, KEYS[m.key]), ('refused_type', 'companion', 1, 'type')
        )
        self.outcome((DATA / 'companion.txt').read_bytes())

    def test_old_info_files_parse_as_before(self):
        """Every info.txt the repository's tools write, and the formats STORAGE.md documents."""
        from xy_forever.tfw_extract import entry_name

        image = bytes(range(256)) * 9
        texts = [(DATA / n).read_bytes() for n in sorted(p.name for p in DATA.glob('legacy-*.txt'))]
        texts += [p.read_bytes() for p in DATA.glob('firmware-*.txt')]
        texts.append(E.info_text('OP-XY 1.2.5', '1.2.5+0', 'open-xy', image).encode())
        texts.append(E.info_text(entry_name('1.2.5+0'), '1.2.5+0', 'official', image).encode())
        texts.append(E.info_text('Custom', '', 'custom', image).encode())  # no version line
        texts.append(sim.info_text(image, 'OP-XY 1.2.1', '1.2.1'))
        texts.append(b'name=OP-XY 1.2.1\nbytes=%d\ncrc32=%08x\n' % (len(image), zlib.crc32(image)))  # device_pass
        texts.append(b'name=Test image\nversion=9.9.9+0\nkind=official\nbytes=3\ncrc32=%08x\n' % zlib.crc32(b'abc'))
        for path in (paths.SESSIONS / 'simulator/yaffs2/xy-forever/firmware').glob('*/info.txt'):
            texts.append(path.read_bytes())  # private, when present
        for text in texts:
            with self.subTest(text=text):
                self.assertEqual(self.outcome(text)[0], 'ok')
                info = self.legacy_same(text)
                self.assertTrue(info.has_bytes or b'bytes=' not in text)
        crlf = self.legacy_same((DATA / 'legacy-crlf.txt').read_bytes())
        self.assertEqual((crlf.name, crlf.version, crlf.kind, crlf.bytes), (b'Custom build', b'9.9.9+0', 3, 12))

    def test_malformed(self):
        good = b'name=x\n'
        cases = [
            (b'', 'empty', 0, None),
            (b'# only a comment\n\n   \n', 'empty', 0, None),
            (b'name=x', 'no_newline', 1, None),
            (b'name=x\nversion=1', 'no_newline', 2, None),
            (b'name=x\r', 'no_newline', 1, None),
            (b'name=x\r\r\n', 'bad_byte', 1, None),
            (b'name=x\rversion=1\n', 'bad_byte', 1, None),
            (b'name=\xc3\xa9\n', 'bad_byte', 1, None),
            (b'# caf\xc3\xa9\nname=x\n', 'bad_byte', 1, None),
            (b'name=a\x00b\n', 'bad_byte', 1, None),
            (b'name=a\x7fb\n', 'bad_byte', 1, None),
            (b'name=a\tb\n', 'bad_byte', 1, None),
            (good + b'noequals\n', 'syntax', 2, None),
            (good + b'name x=1\n', 'syntax', 2, None),
            (good + b'version \n', 'syntax', 2, None),
            (good + b'=x\n', 'bad_key', 2, None),
            (good + b'Name=x\n', 'bad_key', 2, None),
            (good + b'na-me=x\n', 'bad_key', 2, None),
            (good + b'k' * 32 + b'=1\n', 'bad_key', 2, None),
            (good + b'name=y\n', 'duplicate_key', 2, 'name'),
            (b'future=1\nfuture=2\nname=x\n', 'duplicate_key', 2, None),
            (good + b'name=' + b'n' * 48 + b'\n', 'duplicate_key', 2, 'name'),
            (b'name=' + b'n' * 48 + b'\n', 'value_too_long', 1, 'name'),
            (b'version=' + b'v' * 32 + b'\n', 'value_too_long', 1, 'version'),
            (b'future=' + b'v' * 128 + b'\n', 'value_too_long', 1, None),
            (b'name=\n', 'empty_value', 1, 'name'),
            (b'name=   \n', 'empty_value', 1, 'name'),
            (b'bytes=-1\n', 'bad_number', 1, 'bytes'),
            (b'bytes=+1\n', 'bad_number', 1, 'bytes'),
            (b'bytes=007\n', 'bad_number', 1, 'bytes'),
            (b'bytes=4294967296\n', 'bad_number', 1, 'bytes'),
            (b'bytes=12345678901\n', 'bad_number', 1, 'bytes'),
            (b'bytes=1 2\n', 'bad_number', 1, 'bytes'),
            (b'crc32=9F1C0A42\n', 'bad_hex', 1, 'crc32'),
            (b'crc32=9f1c0a4\n', 'bad_hex', 1, 'crc32'),
            (b'crc32=0x9f1c0a\n', 'bad_hex', 1, 'crc32'),
            (b'sha256=' + b'a' * 63 + b'\n', 'bad_hex', 1, 'sha256'),
            (b'needs_os=' + b'g' * 64 + b'\n', 'bad_hex', 1, 'needs_os'),
            (b'kind=Official\n', 'bad_value', 1, 'kind'),
            (b'type=mod\nmachine=bass\n', 'bad_value', 2, 'machine'),
            (b'type=mod-base\nprofile=OPXY\n', 'bad_value', 2, 'profile'),
            (b'type=mod-base\ngeneration=..\n', 'bad_value', 2, 'generation'),
            (b'type=mod-base\ngeneration=kit/1\n', 'bad_value', 2, 'generation'),
            (b'type=xyf-stage2\n', 'unknown_type', 1, 'type'),
            (b'type=content-pack\n', 'unknown_type', 1, 'type'),
            (b'type=Firmware\n', 'unknown_type', 1, 'type'),
            (b'type=companion\n', 'refused_type', 1, 'type'),
            (b'name=x\ntype=companion\nbytes=1\n', 'refused_type', 2, 'type'),
            (b'type=mod\nname=x\nmachine=synth\ngeneration=g\nbytes=3\n', 'key_not_for_type', 5, 'bytes'),
            (
                b'type=mod-base\nmachine=synth\nprofile=p\ngeneration=g\nneeds_os=' + b'a' * 64 + b'\n',
                'key_not_for_type',
                2,
                'machine',
            ),
            (b'machine=synth\n', 'key_not_for_type', 1, 'machine'),
            (b'type=firmware\nname=x\nbytes=1\ncrc32=00000000\n', 'missing_key', 0, 'sha256'),
            (b'type=mod\nname=x\nmachine=synth\n', 'missing_key', 0, 'generation'),
            (b'type=mod-base\nprofile=p\ngeneration=g\n', 'missing_key', 0, 'needs_os'),
            (b'name=x\ngeneration=g\n', 'missing_key', 0, 'needs_os'),
            (b'name=x\nneeds_os=' + b'a' * 64 + b'\n', 'missing_key', 0, 'generation'),
            (b'type=firmware\n', 'missing_key', 0, 'name'),
        ]
        for text, status, line, key in cases:
            with self.subTest(text=text):
                self.assertEqual(self.outcome(text)[:3], (status, line, key))
        # The type survives a refusal (the recogniser names the companion).
        self.assertEqual(TYPES[self.parse(b'type=companion\n').type], 'companion')
        # Tabs and spaces around key, '=' and value are trimmed, as before.
        self.assertEqual(self.outcome(b'\t name \t= \tx y \t\n')[3], {'name': 'x y'})
        # Unknown keys are ignored but still bounded; '#' starts a comment only at a line's start.
        self.assertEqual(self.outcome(b'future=\nname=a # b\n')[3], {'name': 'a # b'})

    def test_bounds(self):
        def text_of(size):  # name=n, then comment lines up to exactly size bytes
            body = b'name=n\n'
            while len(body) + 101 < size:
                body += b'# ' + b'.' * 98 + b'\n'
            return body + b'#' * (size - len(body) - 1) + b'\n'

        self.assertEqual(len(text_of(4096)), 4096)
        self.assertEqual(self.outcome(text_of(4096))[0], 'ok')
        self.assertEqual(self.outcome(text_of(4097))[:2], ('too_large', 0))
        self.assertEqual(self.outcome(b'#' * 255 + b'\nname=x\n')[0], 'ok')
        self.assertEqual(self.outcome(b'#' * 255 + b'\r\nname=x\n')[0], 'ok')
        self.assertEqual(self.outcome(b'#' * 256 + b'\nname=x\n')[:2], ('line_too_long', 1))
        self.assertEqual(self.outcome(b'k' * 31 + b'=1\nname=x\n')[0], 'ok')
        self.assertEqual(self.outcome(b'future=' + b'v' * 127 + b'\n')[0], 'ok')
        self.assertEqual(self.outcome(b'name=' + b'n' * 47 + b'\nversion=' + b'v' * 31 + b'\n')[0], 'ok')
        self.assertEqual(self.outcome(b'source=' + b's' * 127 + b'\nlicence=' + b'l' * 63 + b'\n')[0], 'ok')
        self.assertEqual(self.outcome(b'licence=' + b'l' * 64 + b'\n')[:3], ('value_too_long', 1, 'licence'))
        self.assertEqual(self.outcome(b'bytes=4294967295\n')[3], {'bytes': 4294967295})
        self.assertEqual(self.outcome(b'bytes=0\n')[3], {'bytes': 0})
        keys = b''.join(b'k%d=1\n' % i for i in range(32))
        self.assertEqual(self.outcome(keys)[::3], ('ok', {}))  # unknown keys only: a bare entry
        self.assertEqual(self.outcome(keys[: keys.index(b'k31=')] + b'name=x\n')[0], 'ok')
        self.assertEqual(self.outcome(keys + b'name=x\n')[:3], ('too_many_keys', 33, 'name'))

    def test_random_inputs_agree_and_never_change_old_answers(self):
        """Mutated manifests: the device reader and the twin agree on every answer, and whenever the
        strict reader accepts a text xyf_store::parse_info reads the same fields from it."""
        rng = random.Random(41)
        seeds = [p.read_bytes() for p in sorted(DATA.glob('*.txt'))]
        alphabet = b'=\n\r\t #aZ09_-.+\xc3\x00' + bytes(range(0x61, 0x7B))
        for _ in range(3000):
            text = bytearray(rng.choice(seeds))
            for _ in range(rng.randint(1, 4)):
                op, at = rng.randrange(4), rng.randrange(len(text) + 1)
                if op == 0 and text:
                    del text[min(at, len(text) - 1)]
                elif op == 1:
                    text[at:at] = bytes([rng.choice(alphabet)])
                elif op == 2 and text:
                    a = rng.randrange(len(text))
                    text[a:a] = text[a : a + rng.randint(1, 40)]
                else:
                    text = text[:at]
            if rng.random() < 0.8 and not text.endswith(b'\n'):
                text += b'\n'  # mostly whole files: reach the per-line rules
            text = bytes(text)
            status = self.outcome(text)[0]
            if status == 'ok':
                self.legacy_same(text)

    # -- files.sha256 ---------------------------------------------------------------------------
    def listing(self, data, chunk=4096, stop_at=0xFFFFFFFF):
        status, raw, count, lines = self.lib.list(data, chunk, stop_at)
        entries, at = [], 0
        while at < len(raw):
            end = raw.index(b'\0', at + 33)
            entries.append((raw[at : at + 32].hex(), raw[at + 33 : end].decode(), raw[at + 32]))
            at = end + 1
        status = E.LIST_STATUS[status]
        if stop_at == 0xFFFFFFFF:
            try:
                twin = ('ok', [(h, p) for h, p in E.read_files_sha256(data)])
            except E.ListError as error:
                twin = (error.status, error.line)
            self.assertEqual(
                twin, ('ok', [(h, p) for h, p, _ in entries]) if status == 'ok' else (status, lines), data[:200]
            )
        return status, entries, lines, count

    def test_list_reads_shasum_format_in_any_pieces(self):
        h = [hashlib.sha256(b'%d' % i).hexdigest().encode() for i in range(4)]
        data = (
            h[0]
            + b'  content/presets/drumline/that tom tom tom.json\n'
            + h[1]
            + b' *content/samples/#1.wav\n'
            + h[2]
            + b'  objects/fm2op.o\n'
            + h[3]
            + b'  svg/fm2op.svg\n'
        )
        for chunk in (1, 2, 7, 66, 4096):
            status, entries, lines, count = self.listing(data, chunk)
            self.assertEqual((status, lines, count), ('ok', 4, 4))
            self.assertEqual(
                [p for _, p, _ in entries],
                [
                    'content/presets/drumline/that tom tom tom.json',
                    'content/samples/#1.wav',
                    'objects/fm2op.o',
                    'svg/fm2op.svg',
                ],
            )
            self.assertEqual([b for _, _, b in entries], [0, 1, 0, 0])
            self.assertEqual(entries[2][0], h[2].decode())
        status, entries, _, _ = self.listing(data, 5, stop_at=2)
        self.assertEqual((status, len(entries)), ('stopped', 2))

    def test_list_malformed(self):
        h = b'ab' * 32
        line = lambda path, sep=b'  ', digest=h: digest + sep + path + b'\n'
        cases = [
            (b'', 'empty', 0),
            (line(b'a'), 'ok', 1),
            (line(b'a')[:-1], 'no_newline', 1),
            (line(b'a') + line(b'b')[:-1], 'no_newline', 2),
            (line(b'a', digest=b'AB' * 32), 'bad_hex', 1),
            (line(b'a', digest=h[:62]), 'bad_hex', 1),
            (b'\\' + line(b'a\\nb'), 'bad_hex', 1),  # shasum's escaped form
            (b'SHA256 (a) = ' + h + b'\n', 'bad_hex', 1),  # BSD --tag form
            (b'\n', 'bad_hex', 1),
            (line(b'a', sep=b' '), 'syntax', 1),
            (line(b'a', sep=b'\t '), 'syntax', 1),
            (line(b'a', sep=b' +'), 'syntax', 1),
            (h + b'  \n', 'bad_path', 1),
            (line(b'/etc/passwd'), 'bad_path', 1),
            (line(b'a//b'), 'bad_path', 1),
            (line(b'a/'), 'bad_path', 1),
            (line(b'../a'), 'bad_path', 1),
            (line(b'a/./b'), 'bad_path', 1),
            (line(b'a/b.'), 'bad_path', 1),
            (line(b'a /b'), 'bad_path', 1),
            (line(b'a\\b'), 'bad_path', 1),
            (line(b'c:b'), 'bad_path', 1),
            (line(b'a\rb'), 'bad_path', 1),
            (line(b'a\r'), 'bad_path', 1),
            (line(b'a\tb'), 'bad_path', 1),
            (line(b'caf\xc3\xa9'), 'bad_path', 1),
            (line(b'files.sha256'), 'refused_path', 1),
            (line(b'a/files.sha256'), 'ok', 1),
            (line(b'blackfin/bin/BootLoader.ldr'), 'refused_path', 1),
            (line(b'b') + line(b'b'), 'duplicate', 2),
            (line(b'b') + line(b'a'), 'order', 2),
            (line(b'a/b') + line(b'a-b'), 'order', 2),  # '-' sorts before '/'
            (line(b'B') + line(b'a'), 'ok', 2),  # bytewise, not by case
        ]
        for data, status, lines in cases:
            with self.subTest(data=data):
                got = self.listing(data)
                self.assertEqual((got[0], got[2]), (status, lines))

    def test_list_bounds(self):
        h = b'cd' * 32
        self.assertEqual(self.listing(h + b'  ' + b'p' * 160 + b'\n')[0], 'ok')
        self.assertEqual(self.listing(h + b'  ' + b'p' * 161 + b'\n')[:3], ('line_too_long', [], 1))
        self.assertEqual(self.listing(h + b'  a\n' + h + b'  ' + b'p' * 161)[:3][::2], ('line_too_long', 2))
        self.assertEqual(self.listing(h + b'  ' + b'p' * 200 + b'\n', chunk=3)[:3], ('line_too_long', [], 1))
        many = b''.join(h + b'  f%05d\n' % i for i in range(4097))
        self.assertEqual(self.listing(many[: -len(h) - 9])[0], 'ok')
        status, entries, lines, count = self.listing(many)
        self.assertEqual((status, lines, count), ('too_many', 4097, 4096))

    def test_list_a_content_set(self):
        """About 839 content files: the real names (private ZIP, when present) or a synthetic set."""
        names = [b'content/%s/item %03d.json' % (b'presets' if i % 3 else b'samples', i) for i in range(839)]
        zip_path = paths.DECRYPTED / 'opxy_firmware_1_2_1/firmware_secure.zip'
        if zip_path.is_file():
            import zipfile

            with zipfile.ZipFile(zip_path) as z:
                names = [
                    i.filename.split('firmware/blackfin/', 1)[1].encode()
                    for i in z.infolist()
                    if not i.is_dir() and '/content/' in i.filename
                ]
        names.sort()
        data = b''.join(hashlib.sha256(n).hexdigest().encode() + b'  ' + n + b'\n' for n in names)
        status, entries, _, count = self.listing(data, chunk=512)
        self.assertEqual((status, count), ('ok', len(names)))
        self.assertGreaterEqual(count, 839)
        self.assertEqual(entries[100], (hashlib.sha256(names[100]).hexdigest(), names[100].decode(), 0))

    def test_device_reads_a_content_manifest(self):
        """content.sha256 from the host writer in the device reader's order, any pieces."""
        from xy_forever import content_manifest as CM

        names = ['B.wav', 'a/b', 'a-b', 'a b/c', 'a.b', 'a!', 'Z/#1.aif', '_x', '~y', 'd/' + 'e' * 158]
        with tempfile.TemporaryDirectory() as tmp:
            for i, rel in enumerate(names):
                (Path(tmp) / rel).parent.mkdir(parents=True, exist_ok=True)
                (Path(tmp) / rel).write_bytes(b'%d' % i)
            data = CM.list_text(CM.scan(tmp))
        for chunk in (1, 66, 4096):
            status, entries, _, count = self.listing(data, chunk)
            self.assertEqual((status, count), ('ok', len(names)))
            self.assertEqual([p for _, p, _ in entries], sorted(names, key=str.encode))

    def test_host_list_matches_shasum(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'fm2op'
            for rel, data in (
                ('objects/fm2op.o', b'\x7fELF'),
                ('svg/fm2op.svg', b'<svg/>'),
                ('info.txt', b'x\n'),
                ('presets/a b.json', b'{}'),
                ('files.sha256', b'old'),
            ):
                (folder / rel).parent.mkdir(parents=True, exist_ok=True)
                (folder / rel).write_bytes(data)
            text = E.files_sha256_text(folder)
            status, entries, _, _ = self.listing(text.encode(), chunk=13)
            self.assertEqual(status, 'ok')
            self.assertEqual(
                [p for _, p, _ in entries], ['info.txt', 'objects/fm2op.o', 'presets/a b.json', 'svg/fm2op.svg']
            )
            shasum = shutil.which('shasum')
            if shasum:
                rels = [p for _, p, _ in entries]
                made = subprocess.run([shasum, '-a', '256', *rels], cwd=folder, check=True, capture_output=True).stdout
                self.assertEqual(made.decode(), text)

    # -- recogniser ----------------------------------------------------------------------------
    def file(self, name, data, size=None, handed=0x10C):
        prefix = data[:handed]
        kind, reason, accepted, _ = self.lib.file(
            name.encode() if isinstance(name, str) else name, len(data) if size is None else size, prefix
        )
        return FILE_KINDS[kind], REASONS[reason], accepted

    def folder(self, names, info=None, name='package', vendor_pack=False):
        m = None
        if info is not None:
            m = self.parse(info)
        kind, reason, accepted, content_set = self.lib.folder(
            name.encode(), [n.encode() for n in names], m, vendor_pack
        )
        return FILE_KINDS[kind], REASONS[reason], accepted, content_set

    def test_valid_names(self):
        for good in ('opxy_firmware_1_2_5.tfw', 'a', 'x' * 127, 'that tom.json', '#1'):
            self.assertTrue(self.lib.valid_name(good.encode()), good)
        for bad in (b'', b'x' * 128, b'.', b'..', b'a.', b'a ', b'a/b', b'a\\b', b'c:', b'a\tb', b'caf\xc3\xa9'):
            self.assertFalse(self.lib.valid_name(bad), bad)

    def test_files_by_magic_not_extension(self):
        ldr = ldr_first(4096) + bytes(4080)
        cases = [
            ('opxy_firmware_1_2_5.tfw', tfw_header(), ('official_tfw', 'ok', True)),
            ('anything.bin', tfw_header(), ('official_tfw', 'ok', True)),
            ('bare.tfw', tfw_header(0x40), ('official_tfw', 'ok', True)),
            ('x.tfw', tfw_header(tesg=b'TESH'), ('unknown', 'unknown', False)),
            ('x.tfw', tfw_header(words=(2, 1)), ('unknown', 'unknown', False)),
            ('x.tfw', tfw_header()[:0x40] + bytes(0x340), ('unknown', 'unknown', False)),
            ('os.ldr', ldr, ('ldr_image', 'ok', True)),
            ('firmware.tfw', ldr, ('ldr_image', 'ok', True)),
            ('os.ldr', ldr_first(4000) + bytes(4080), ('unknown', 'unknown', False)),  # length word wrong
            ('os.ldr', ldr_first(4096, 0x5005) + bytes(4080), ('unknown', 'unknown', False)),
            ('os.ldr', bytes([0x04, 0x50, 0x93, 0xAD]) + ldr[4:], ('unknown', 'unknown', False)),  # checksum
            ('main.xys2', b'XYS2' + bytes(92), ('xyf_stage2', 'ok', True)),
            ('main.xys2', b'XYS2' + bytes(10), ('unknown', 'unknown', False)),
            ('build.zip', b'PK\x03\x04' + bytes(60), ('zip', 'host_only', False)),
            ('empty.zip', b'PK\x05\x06' + bytes(18), ('zip', 'host_only', False)),
            ('os.ldr', b'\x00' * 64, ('unknown', 'unknown', False)),
            ('os.ldr', b'', ('unknown', 'unknown', False)),
            ('bootloader.ldr', ldr, ('bootloader', 'refused', False)),
            ('nordic_BOOTLOADER_0.bin', b'x' * 64, ('bootloader', 'refused', False)),
            ('backplane.cyacd', b'0123', ('companion', 'refused', False)),
            ('BLE.bin', b'0123', ('companion', 'refused', False)),
            ('../os.ldr', ldr, ('unknown', 'bad_name', False)),
            ('os.ldr.', ldr, ('unknown', 'bad_name', False)),
        ]
        for name, data, want in cases:
            with self.subTest(name=name, head=data[:8]):
                self.assertEqual(self.file(name, data), want)

    def test_files_never_read_past_what_is_handed(self):
        tfw = tfw_header()
        self.assertEqual(self.file('a.tfw', tfw, handed=0x10B), ('unknown', 'short', False))
        self.assertEqual(self.file('a.tfw', tfw, handed=0x10C), ('official_tfw', 'ok', True))
        self.assertEqual(self.file('a.tfw', tfw, handed=0x43), ('unknown', 'short', False))
        self.assertEqual(self.file('a.tfw', tfw_header(0x40), handed=0xCB), ('unknown', 'short', False))
        self.assertEqual(self.file('os.ldr', ldr_first(64) + bytes(48), handed=15), ('unknown', 'short', False))
        self.assertEqual(self.file('os.ldr', ldr_first(64) + bytes(48), handed=16), ('ldr_image', 'ok', True))
        self.assertEqual(self.file('x', b'XYS2' + bytes(92), handed=3), ('unknown', 'short', False))
        self.assertEqual(self.file('x', b'abc', handed=3), ('unknown', 'unknown', False))
        # A handed length beyond the file's size is clipped to it.
        self.assertEqual(self.file('a.tfw', tfw, size=0x100, handed=0x380), ('unknown', 'short', False))
        # Bytes are passed in an exactly sized buffer: a read past it would be outside it.
        reason = self.lib.file(b'a.tfw', 100000000, bytes((0xBA, 0xBE, 0xCA, 0xFE)))[1]
        self.assertEqual(REASONS[reason], 'short')

    def test_real_magic_bytes(self):
        """Official .tfw headers, the stock LDRs and a built stage 2 (private area; skipped without them)."""
        found = 0
        for path in sorted((paths.FIRMWARE / 'official').glob('*.tfw')):
            with path.open('rb') as f:
                head = f.read(0x10C)
            self.assertEqual(self.file(path.name, head, size=path.stat().st_size), ('official_tfw', 'ok', True))
            found += 1
        for name in ('os.ldr', 'bootloader.ldr'):
            path = paths.STOCK_121 / name
            if path.is_file():
                head = path.read_bytes()[:16]
                want = ('ldr_image', 'ok', True) if name == 'os.ldr' else ('bootloader', 'refused', False)
                self.assertEqual(self.file(name, head, size=path.stat().st_size), want)
                # Renamed: the OS by its magic; the bootloader file is a padded partition image whose
                # first block does not span the file, so it is not an LDR image either.
                renamed = self.file('renamed.bin', head, size=path.stat().st_size)
                self.assertEqual(
                    renamed, ('ldr_image', 'ok', True) if name == 'os.ldr' else ('unknown', 'unknown', False)
                )
                found += 1
        for path in sorted(paths.BUILDS.glob('*/xyf-stage2*/main.xys2'))[:1]:
            self.assertEqual(
                self.file('main.xys2', path.read_bytes()[:96], size=path.stat().st_size), ('xyf_stage2', 'ok', True)
            )
            found += 1
        if not found:
            self.skipTest('no official packages, stock LDRs or builds in the private area')

    def test_folders_by_shape_and_type(self):
        info = lambda name: (DATA / name).read_bytes()
        legacy = info('legacy-xyf-entry.txt')
        cases = [
            (['info.txt', 'os.ldr'], legacy, ('firmware_entry', 'ok', True, False)),
            (
                ['os.ldr', 'info.txt', 'layout.json'],
                info('firmware-official.txt'),
                ('firmware_entry', 'ok', True, False),
            ),
            (['os.ldr'], None, ('firmware_entry', 'ok', True, False)),  # copied in with no info.txt
            (
                ['os.ldr', 'info.txt', 'content.sha256', 'firmware.version', 'content'],
                info('firmware-official.txt'),
                ('firmware_entry', 'ok', True, True),
            ),
            (['os.ldr', 'info.txt', 'content.sha256'], legacy, ('firmware_entry', 'content_set', False, False)),
            (['os.ldr', 'firmware.version'], None, ('firmware_entry', 'content_set', False, False)),
            (['info.txt'], legacy, ('firmware_entry', 'missing_image', False, False)),
            (['info.txt', 'OS.LDR'], legacy, ('firmware_entry', 'missing_image', False, False)),
            (['info.txt', 'os.ldr', 'layout.json'], info('assembled.txt'), ('assembled_entry', 'ok', True, False)),
            (['info.txt', 'os.ldr'], info('assembled.txt'), ('assembled_entry', 'ok', True, False)),
            (['info.txt', 'files.sha256', 'objects'], info('mod-base.txt'), ('mod_base', 'ok', True, False)),
            (
                ['info.txt', 'files.sha256', 'objects', 'svg', 'presets'],
                info('mod-fm2op.txt'),
                ('mod', 'ok', True, False),
            ),
            (['manifest.json', 'presets', 'projects'], None, ('unknown', 'need_manifest', False, False)),
            (['manifest.json', 'os.ldr'], None, ('unknown', 'need_manifest', False, False)),
            (['manifest.json', 'os.ldr'], info('assembled.txt'), ('assembled_entry', 'ok', True, False)),
            (['info.txt', 'manifest.json'], info('mod-fm2op.txt'), ('mod', 'ok', True, False)),
            (['info.txt', 'manifest.json'], b'type=bad\n', ('unknown', 'manifest', False, False)),
            (['info.txt', 'backplane.bin'], info('companion.txt'), ('companion', 'refused', False, False)),
            (['info.txt', 'os.ldr'], b'name=x\nname=y\n', ('unknown', 'manifest', False, False)),
            (['info.txt', 'os.ldr'], b'type=xyf-stage2\n', ('unknown', 'manifest', False, False)),
            (['info.txt', 'os.ldr'], None, ('unknown', 'need_manifest', False, False)),
            (['os.ldr', 'bootloader.ldr'], None, ('bootloader', 'refused', False, False)),
            (['info.txt', 'objects', 'Bootloader'], info('mod-fm2op.txt'), ('bootloader', 'refused', False, False)),
            (['os.ldr', 'ble.bin'], None, ('companion', 'refused', False, False)),
            (['os.ldr', '..'], None, ('unknown', 'bad_name', False, False)),
            (['readme.txt'], None, ('unknown', 'unknown', False, False)),
            ([], None, ('unknown', 'unknown', False, False)),
        ]
        for names, text, want in cases:
            with self.subTest(names=names, text=text and text[:30]):
                self.assertEqual(self.folder(names, text), want)
        self.assertEqual(
            self.folder(['manifest.json', 'presets'], vendor_pack=True), ('content_pack', 'ok', True, False)
        )
        self.assertEqual(
            self.folder(['manifest.json', 'os.ldr'], vendor_pack=True), ('unknown', 'ambiguous', False, False)
        )
        self.assertEqual(self.folder(['os.ldr'], name='a/b'), ('unknown', 'bad_name', False, False))

    # -- dependencies --------------------------------------------------------------------------
    def test_dependency_rules_both_ways(self):
        load = lambda name: self.parse((DATA / name).read_bytes())
        stock, official, base, mod = (
            load('firmware-stock-121.txt'),
            load('firmware-official.txt'),
            load('mod-base.txt'),
            load('mod-fm2op.txt'),
        )
        assembled, legacy = load('assembled.txt'), load('legacy-xyf-entry.txt')
        bad = self.parse(b'name=x\nname=y\n')
        dep = lambda f, a, b: DEPENDENCY[getattr(self.lib, f)(a, b)]
        other_gen = self.parse((DATA / 'mod-fm2op.txt').read_bytes().replace(b'kit-0.0.6', b'kit-0.0.7'))
        other_profile = self.parse((DATA / 'mod-fm2op.txt').read_bytes() + b'profile=opxy-1.2.5\n')
        same_profile = self.parse((DATA / 'mod-fm2op.txt').read_bytes() + b'profile=opxy-1.2.1\n')
        no_sha = self.parse(b'name=OP-XY 1.2.1\nbytes=1\ncrc32=00000000\n')
        # The base (and an assembled set) runs only on the entry whose sha256 is its needs_os.
        self.assertEqual(dep('check_os', base, stock), 'ok')
        self.assertEqual(dep('check_os', assembled, stock), 'ok')
        self.assertEqual(dep('check_os', base, official), 'os_mismatch')
        self.assertEqual(dep('check_os', base, no_sha), 'no_sha256')
        self.assertEqual(dep('check_os', base, legacy), 'no_sha256')
        self.assertEqual(dep('check_os', base, mod), 'not_firmware')
        self.assertEqual(dep('check_os', mod, stock), 'not_dependent')
        self.assertEqual(dep('check_os', stock, stock), 'not_dependent')
        self.assertEqual(dep('check_os', bad, stock), 'invalid')
        self.assertEqual(dep('check_os', base, bad), 'invalid')
        # Mods come from the same kit export (and profile, when named) as the base.
        self.assertEqual(dep('check_mod', base, mod), 'ok')
        self.assertEqual(dep('check_mod', base, same_profile), 'ok')
        self.assertEqual(dep('check_mod', base, other_gen), 'generation')
        self.assertEqual(dep('check_mod', base, other_profile), 'profile')
        self.assertEqual(dep('check_mod', mod, mod), 'not_base')
        self.assertEqual(dep('check_mod', base, base), 'not_mod')
        self.assertEqual(dep('check_mod', base, bad), 'invalid')
        # An assembled set belongs to the base that made it.
        self.assertEqual(dep('check_assembled', base, assembled), 'ok')
        self.assertEqual(dep('check_assembled', base, stock), 'not_dependent')
        self.assertEqual(dep('check_assembled', base, mod), 'not_firmware')
        self.assertEqual(dep('check_assembled', mod, assembled), 'not_base')
        moved = self.parse(
            (DATA / 'assembled.txt').read_bytes().replace(b'generation=kit-0.0.6', b'generation=kit-0.0.5')
        )
        self.assertEqual(dep('check_assembled', base, moved), 'generation')
        other_os = self.parse((DATA / 'assembled.txt').read_bytes().replace(b'needs_os=fb', b'needs_os=fc'))
        self.assertEqual(dep('check_assembled', base, other_os), 'os_mismatch')
        other_set = self.parse(
            (DATA / 'assembled.txt').read_bytes().replace(b'profile=opxy-1.2.1', b'profile=opxy-1.2.5')
        )
        self.assertEqual(dep('check_assembled', base, other_set), 'profile')
        # Apply: the base on the entry, then every mod on the base; the failing mod's index.
        mods = [mod, same_profile, other_gen]
        apply = lambda entry, b, n: (lambda d, failed: (DEPENDENCY[d], failed))(
            *self.lib.check_apply(entry, b, mods, n)
        )
        self.assertEqual(apply(stock, base, 2), ('ok', 2))
        self.assertEqual(apply(stock, base, 3), ('generation', 2))
        self.assertEqual(apply(official, base, 2), ('os_mismatch', 2))
        self.assertEqual(apply(stock, mod, 2), ('not_base', 2))
        self.assertEqual(apply(stock, base, 0), ('ok', 0))
        # Remove lists dependents (it does not refuse).
        self.assertTrue(self.lib.needs(base, stock))
        self.assertTrue(self.lib.needs(assembled, stock))
        self.assertFalse(self.lib.needs(base, official))
        self.assertFalse(self.lib.needs(mod, stock))

    def test_host_writer_emits_only_what_the_device_reads(self):
        fields = E.parse_manifest((DATA / 'mod-base.txt').read_bytes())
        text = E.manifest_text(fields).encode()
        self.assertEqual(self.outcome(text)[3], fields)
        self.assertTrue(text.startswith(b'type=mod-base\nname='))
        for bad in (dict(fields, profile='Bad'), dict(fields, name='café'), dict(type='mod', name='x')):
            with self.subTest(bad=bad), self.assertRaises(E.ManifestError):
                E.manifest_text(bad)


if __name__ == '__main__':
    unittest.main()
