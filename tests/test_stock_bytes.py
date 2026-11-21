"""No teenage engineering bytes in a release image (#178): xy_forever/stock_bytes.py on synthetic images, on this
source's verified product build against the private stock pair, and scripts/web_release.py's pin and staging."""
import hashlib
import json
from pathlib import Path
import random
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
from xy_forever import paths, stock_bytes as S  # noqa: E402
import web_release  # noqa: E402


def noise(n, seed):
    return random.Random(seed).randbytes(n)


class SharedRunsTest(unittest.TestCase):
    def setUp(self):
        self.stock = noise(200_000, 1)
        self.ours = noise(60_000, 2)

    def test_a_copied_run_is_found_with_its_place(self):
        image = self.ours[:1000] + self.stock[5003:5003 + 64] + self.ours[1000:]
        self.assertEqual(S.shared_runs(image, {'os.ldr': self.stock}), [(64, 1000, 'os.ldr', 5003)])

    def test_a_shorter_run_does_not_count(self):
        image = self.ours[:1000] + self.stock[5003:5003 + S.MIN_RUN - 1] + self.ours[1000:]
        self.assertEqual(S.shared_runs(image, {'os.ldr': self.stock}), [])

    def test_a_long_copy_counts_once(self):
        image = self.ours[:10] + self.stock[777:777 + 5000] + self.ours[10:]
        self.assertEqual(S.shared_runs(image, {'s': self.stock}), [(5000, 10, 's', 777)])

    def test_public_tables_and_filler_do_not_count(self):
        tables = b''.join(S.public_tables())
        stock = self.stock[:5000] + tables + bytes(4096) + b'\xff' * 4096 + self.stock[5000:]
        image = self.ours[:100] + tables + bytes(4096) + b'\xff' * 4096 + self.ours[100:]
        self.assertEqual(S.shared_runs(image, {'s': stock}), [])

    def test_the_tables_are_the_public_ones(self):
        self.assertEqual(S.crc32_table()[1], 0x77073096)
        self.assertEqual(S.sha256_constants()[:2] + S.sha256_constants()[-1:], [0x428a2f98, 0x71374491, 0xc67178f2])


class ReleaseImageTest(unittest.TestCase):
    def stocks(self):
        files = [paths.STOCK_121 / n for n in ('os.ldr', 'bootloader.ldr')]
        if not all(f.is_file() for f in files):
            self.skipTest('the private stock 1.2.1 pair is absent (setup.py import-stock)')
        return {f.name: f.read_bytes() for f in files}

    def test_this_sources_product_build_has_no_stock_bytes(self):
        stocks = self.stocks()
        build = paths.newest_verified_build()
        if build is None:
            self.skipTest('no verified product build of this source (scripts/check.py builds one)')
        self.assertEqual(S.shared_runs((build / 'candidate.ldr').read_bytes(), stocks), [])
        self.assertTrue(S.shared_runs(stocks['os.ldr'][:400_000], stocks))     # the check sees stock bytes

    def test_the_pinned_release_has_no_stock_bytes(self):
        pin = json.loads(web_release.PIN.read_text())
        if not pin['sha256']:
            self.skipTest('no release pinned')
        self.assertEqual(pin['image'], pin['sha256'][:8])
        found = [b for b in paths.BUILDS.glob(pin['image']) if (b / 'candidate.ldr').is_file()]
        if not found:
            self.skipTest('the pinned image %s is not built here' % pin['image'])
        data = (found[0] / 'candidate.ldr').read_bytes()
        self.assertEqual(hashlib.sha256(data).hexdigest(), pin['sha256'])
        self.assertEqual(S.shared_runs(data, self.stocks()), [])

    def test_stage_pins_and_copies_a_verified_build(self):
        build = paths.newest_verified_build()
        if build is None:
            self.skipTest('no verified product build of this source')
        with tempfile.TemporaryDirectory() as tmp:
            pin_path, staged = Path(tmp) / 'release.json', Path(tmp) / 'public/release/os.ldr'
            pin_path.write_text('{"schema": 1, "version": "", "image": "", "sha256": "", "bytes": 0}')
            stocks = {'os.ldr': noise(100_000, 3)}
            with self.assertRaisesRegex(ValueError, 'give --notes'):
                web_release.stage(build, stocks, pin_path, staged)
            self.assertFalse(staged.exists())
            pin = web_release.stage(build, stocks, pin_path, staged, notes='  Faster updates.  ')
            self.assertEqual(pin['notes'], 'Faster updates.')
            self.assertEqual(web_release.stage(build, stocks, pin_path, staged), pin)
            self.assertEqual(json.loads(pin_path.read_text()), pin)
            self.assertEqual(staged.read_bytes(), (build / 'candidate.ldr').read_bytes())
            self.assertIsNone(web_release.check(pin_path, staged))
            staged.write_bytes(b'other')
            self.assertIn('not the pinned', web_release.check(pin_path, staged))
            data = (build / 'candidate.ldr').read_bytes()
            with self.assertRaisesRegex(ValueError, 'shares stock bytes'):
                web_release.stage(build, {'os.ldr': data[1000:3000]}, pin_path, staged)
            with self.assertRaisesRegex(ValueError, 'not a verified product build'):
                web_release.stage(Path(tmp), stocks, pin_path, staged)


if __name__ == '__main__':
    unittest.main()
