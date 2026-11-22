"""Content-set manifests (xy_forever/content_manifest.py): synthetic folders made here;
the official sets in the private area are checked only when present (nothing from them is kept)."""
import contextlib
import hashlib
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from xy_forever import content_manifest as CM, paths  # noqa: E402
from xy_forever.xyf_entry import read_files_sha256  # noqa: E402

FILES = {'a/b.txt': b'one', 'a-b.txt': b'two', 'a b/c.json': b'{}', 'B.wav': b'\0' * 1000,
         'samples/#1/x.aif': b'xyz', 'op1.json': b'[]'}
OFFICIAL = (('1.1.33', 'opxy_firmware_1_1_33/extracted/firmware/blackfin/content'),
            ('1.2.1', 'opxy_firmware_1_2_1/extracted/firmware/blackfin/content'),
            ('1.2.5', 'opxy_firmware_1_2_5/firmware/blackfin/content'))


def make_package(base, files=FILES, version=b'9.8.7+0\n'):
    """base/firmware/{firmware.version, blackfin/content/...}, the extracted package layout."""
    content = Path(base) / 'firmware/blackfin/content'
    content.mkdir(parents=True)
    if version is not None:
        (Path(base) / 'firmware/firmware.version').write_bytes(version)
    for rel, data in files.items():
        (content / rel).parent.mkdir(parents=True, exist_ok=True)
        (content / rel).write_bytes(data)
    return content


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = CM.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class ContentManifestTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='xyf-content-'))
        self.addCleanup(shutil.rmtree, self.tmp)

    def test_well_formed(self):
        content = make_package(self.tmp / 'pkg')
        code, out, _ = run('content-manifest', str(content), '--out', str(self.tmp / 'm'))
        self.assertEqual(code, 0)
        data = (self.tmp / 'm/content.sha256').read_bytes()
        order = sorted(p.encode() for p in FILES)
        self.assertEqual([p.encode() for _, p in read_files_sha256(data)], order)
        self.assertEqual([p for _, p in read_files_sha256(data)][:3], ['B.wav', 'a b/c.json', 'a-b.txt'])
        self.assertEqual(data.split(b'\n')[0], hashlib.sha256(FILES['B.wav']).hexdigest().encode() + b'  B.wav')
        total = sum(len(v) for v in FILES.values())
        self.assertEqual(out.strip(), 'version=9.8.7+0 files=%d bytes=%d list_sha256=%s'
                         % (len(FILES), total, hashlib.sha256(data).hexdigest()))
        self.assertEqual((self.tmp / 'm/firmware.version').read_bytes(), b'9.8.7+0\n')
        self.assertEqual((self.tmp / 'm/content.sizes').read_bytes().split(b'\n')[0], b'1000  B.wav')
        shasum = shutil.which('shasum')
        if shasum:
            made = subprocess.run([shasum, '-a', '256', *[p.decode() for p in order]], cwd=content, check=True,
                                  capture_output=True).stdout
            self.assertEqual(made, data)
        # The longest allowed path: 160 bytes.
        longest = '/'.join(['d' * 39] * 4)
        self.assertEqual(len(longest), 159)
        make_package(self.tmp / 'long', {longest + 'x': b'1'}, version=None)
        CM.write_manifest(self.tmp / 'long/firmware/blackfin/content', self.tmp / 'm2', self.tmp / 'm/firmware.version')

    def test_refusals(self):
        long = '/'.join(['d' * 40] * 4) + 'x'                     # 164 bytes
        cases = [({long: b'1'}, 'longer than 160'),
                 ({'café.wav': b'1'}, 'printable ASCII'),
                 ({'a\tb': b'1'}, 'printable ASCII'),
                 ({'a\\b': b'1'}, 'printable ASCII'),
                 ({'a/b.': b'1'}, 'ending in'),
                 ({'a /b': b'1'}, 'ending in'),
                 ({'x/BootLoader.ldr': b'1'}, 'refused name'),
                 ({'files.sha256': b'1'}, 'refused name'),
                 ({}, 'empty content set')]
        for i, (files, message) in enumerate(cases):
            with self.subTest(files=files):
                content = make_package(self.tmp / str(i), files)
                code, _, err = run('content-manifest', str(content), '--out', str(self.tmp / ('o%d' % i)))
                self.assertEqual(code, 2)
                self.assertIn(message, err)
                self.assertFalse((self.tmp / ('o%d' % i)).exists())
        (make_package(self.tmp / 'e', {}) / 'only/empty/folders').mkdir(parents=True)
        with self.assertRaisesRegex(CM.ContentError, 'empty content set'):
            CM.scan(self.tmp / 'e/firmware/blackfin/content')
        with self.assertRaisesRegex(CM.ContentError, 'printable'):
            CM.check_paths([b'ok', 'café'.encode()])
        # Two paths one file apart on a case-insensitive disk (made here as a list: this Mac's disk
        # may not hold both).
        with self.assertRaisesRegex(CM.ContentError, 'duplicate paths after normalisation'):
            CM.check_paths([b'Samples/a.wav', b'samples/A.wav'])
        with self.assertRaisesRegex(CM.ContentError, 'at most 4096'):
            CM.check_paths([b'f%05d' % i for i in range(4097)])
        self.assertEqual(len(CM.check_paths([b'f%05d' % i for i in range(4096)])), 4096)

    def test_symlinks_and_version(self):
        content = make_package(self.tmp / 'pkg')
        (content / 'link.wav').symlink_to(content / 'op1.json')
        with self.assertRaisesRegex(CM.ContentError, 'symlink refused: link.wav'):
            CM.scan(content)
        (content / 'link.wav').unlink()
        (content / 'linked').symlink_to(content / 'a', target_is_directory=True)
        with self.assertRaisesRegex(CM.ContentError, 'symlink refused: linked'):
            CM.scan(content)
        (content / 'linked').unlink()
        alias = self.tmp / 'alias'
        alias.symlink_to(content, target_is_directory=True)
        with self.assertRaisesRegex(CM.ContentError, 'not a folder'):
            CM.scan(alias)
        os.remove(self.tmp / 'pkg/firmware/firmware.version')
        code, _, err = run('content-manifest', str(content), '--out', str(self.tmp / 'm'))
        self.assertEqual((code, 'no version file' in err), (2, True))
        (self.tmp / 'v').write_bytes(b'two words\n')
        with self.assertRaisesRegex(CM.ContentError, 'printable token'):
            CM.write_manifest(content, self.tmp / 'm', self.tmp / 'v')
        with self.assertRaisesRegex(CM.ContentError, 'outside every git checkout'):
            CM.outside_checkouts(ROOT / 'tests' / 'never-written')

    def test_compare(self):
        a = make_package(self.tmp / 'a')
        changed = dict(FILES, **{'new/n.bin': b'12345', 'op1.json': b'[1, 2]'})
        del changed['a-b.txt']
        b = make_package(self.tmp / 'b', changed)
        for name, content in (('a', a), ('b', b)):
            CM.write_manifest(content, self.tmp / name / 'm')
        ma, mb = str(self.tmp / 'a/m/content.sha256'), str(self.tmp / 'b/m/content.sha256')
        want = dict(only_a=dict(count=1, bytes=3, paths=['a-b.txt']),
                    only_b=dict(count=1, bytes=5, paths=['new/n.bin']),
                    differ=dict(count=1, bytes_a=2, bytes_b=6, paths=['op1.json']), identical=False)
        for x, y in ((ma, mb), (ma, str(b)), (str(a), mb), (str(a), str(b))):
            with self.subTest(a=x, b=y):
                self.assertEqual(CM.compare(CM.load(x), CM.load(y)), want)
                code, out, _ = run('content-compare', x, y)
                self.assertEqual(code, 1)
                self.assertNotIn('paths', out)
        code, out, _ = run('content-compare', ma, str(a), '--paths')
        self.assertEqual(code, 0)
        self.assertIn('"identical": true', out)
        # Without content.sizes a list still compares; its byte totals are unknown.
        os.remove(self.tmp / 'a/m/content.sizes')
        got = CM.compare(CM.load(ma), CM.load(mb))
        self.assertEqual((got['only_a']['bytes'], got['only_b']['bytes'], got['differ']['bytes_a']), (None, 5, None))
        (self.tmp / 'b/m/content.sizes').write_bytes(b'1  other\n')
        with self.assertRaisesRegex(CM.ContentError, 'does not match'):
            CM.load(mb)
        (self.tmp / 'bad.sha256').write_bytes(b'ab' * 32 + b'  b\n' + b'ab' * 32 + b'  a\n')
        code, _, err = run('content-compare', str(self.tmp / 'bad.sha256'), ma)
        self.assertEqual((code, 'order' in err), (2, True))


@pytest.mark.gate
class OfficialContentTest(unittest.TestCase):
    """Every official set present in the private area passes the device list rules (no output kept)."""

    def test_official_sets(self):
        found = [(v, paths.DECRYPTED / rel) for v, rel in OFFICIAL if (paths.DECRYPTED / rel).is_dir()]
        if not found:
            self.skipTest('no extracted official content in the private area')
        for version, content in found:
            with self.subTest(version=version):
                entries = CM.scan(content)
                files = sum(len(f) for _, _, f in os.walk(content))
                self.assertEqual(len(entries), files)
                listed = read_files_sha256(CM.list_text(entries))
                self.assertEqual(len(listed), files)
                self.assertTrue(CM.read_version(content)[1].startswith(version + '+'))


if __name__ == '__main__':
    unittest.main()
