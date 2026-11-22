"""A checkout's verified build: its own build/product first, else the builds/<image> of its sources verified last
(builds are named by their image, builds/<hash8>/); without one, a test skips naming why (fails under check)."""

import contextlib
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from conftest import verified_build

from xy_forever import paths, xyf_build

SOURCE = {'target/a.cpp': '11', 'xyf_build.py': '22'}


def make(builds, data, passed=True, mode='xyf-product', source=SOURCE, name=None, when=0):
    digest = hashlib.sha256(data).hexdigest()
    d = builds / (name or digest[:8])
    d.mkdir()
    (d / 'candidate.ldr').write_bytes(data)
    (d / 'manifest.json').write_text(
        json.dumps({'image': digest[:8], 'candidate_sha256': digest, 'source_sha256': source})
    )
    (d / 'verification.json').write_text(json.dumps({'passed': passed, 'mode': mode}))
    os.utime(d / 'verification.json', (when, when))
    return d


def isolated(tmp, source=SOURCE):
    """paths pointed at a temporary BUILDS and build/product, with this checkout's sources as given."""
    stack = contextlib.ExitStack()
    builds, product = Path(tmp) / 'builds', Path(tmp) / 'build/product'
    builds.mkdir(parents=True, exist_ok=True)
    stack.enter_context(mock.patch.object(paths, 'BUILDS', builds))
    stack.enter_context(mock.patch.object(paths, 'PRODUCT', product))
    stack.enter_context(mock.patch.object(xyf_build, 'source_hashes', lambda: source))
    return stack, builds, product


class NewestVerifiedBuild(unittest.TestCase):
    def test_another_checkout_replacing_the_shared_image_leaves_this_ones_build(self):
        """The #229 bug: a worktree built the same image from other sources and replaced BUILDS/<image>; this
        checkout's build/product (which no other checkout writes) still counts."""
        with tempfile.TemporaryDirectory() as tmp:
            stack, builds, product = isolated(tmp)
            with stack:
                product.parent.mkdir()
                own = make(product.parent, b'image', name='product')
                other = make(builds, b'image', source=dict(SOURCE, **{'target/a.cpp': '33'}))
                self.assertEqual(paths.build_image(other)[0], json.loads((own / 'manifest.json').read_text())['image'])
                self.assertEqual(paths.verified_build(), (product, None))
                self.assertEqual(paths.newest_verified_build(), product)

    def test_without_a_usable_build_the_reason_is_named(self):
        with tempfile.TemporaryDirectory() as tmp:
            stack, builds, product = isolated(tmp)
            with stack:
                self.assertRegex(paths.verified_build()[1], 'no .*build/product; mise run verify makes one')
                product.parent.mkdir()
                make(product.parent, b'image', name='product', source=dict(SOURCE, **{'target/a.cpp': '33'}))
                self.assertRegex(paths.verified_build()[1], 'of other sources')
                (product / 'verification.json').write_text(json.dumps({'passed': False, 'mode': 'xyf-product'}))
                self.assertRegex(paths.verified_build()[1], 'did not pass --verify')
                self.assertIsNone(paths.newest_verified_build())

    def test_the_test_helper_skips_naming_why_or_fails_under_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            stack, _, _ = isolated(tmp)
            with stack, mock.patch.dict(os.environ, {paths.REQUIRE_BUILD: ''}):
                with self.assertRaisesRegex(unittest.SkipTest, 'mise run verify makes one'):
                    verified_build(self)
                os.environ[paths.REQUIRE_BUILD] = '1'
                with self.assertRaisesRegex(AssertionError, 'mise run verify makes one'):
                    verified_build(self)

    def test_else_the_last_verified_of_this_source_in_builds_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            stack, builds, _ = isolated(tmp)
            with stack:
                make(builds, b'a', when=1000)
                want = make(builds, b'b', when=2000)
                make(builds, b'c', passed=False, when=3000)
                make(builds, b'd', source=dict(SOURCE, **{'target/a.cpp': '33'}), when=4000)  # another branch's
                make(builds, b'e', mode='xyf-mtp-usb', when=5000)
                make(builds, b'f', name='xy-forever-product-195-001', when=6000)  # the old numbered names
                make(builds, b'g', name='0badc0de', when=7000)  # not its image
                self.assertEqual(paths.newest_verified_build(), want)
            stack, _, _ = isolated(tmp, dict(SOURCE, **{'xyf_build.py': '44'}))
            with stack:
                self.assertIsNone(paths.newest_verified_build())  # this checkout's source changed: none counts

    def test_name_by_image_moves_and_replaces(self):
        with tempfile.TemporaryDirectory() as tmp:
            builds = Path(tmp)
            staged = make(builds, b'x', name='check-123')
            old = builds / hashlib.sha256(b'x').hexdigest()[:8]
            old.mkdir()
            (old / 'stale').write_text('')
            with mock.patch.object(paths, 'BUILDS', builds):
                moved = paths.name_by_image(staged)
            self.assertEqual(moved, old)
            self.assertFalse(staged.exists())
            self.assertFalse((moved / 'stale').exists())
            self.assertEqual(paths.build_image(moved)[0], moved.name)


if __name__ == '__main__':
    unittest.main()
