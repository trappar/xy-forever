"""Builds are named by their image (builds/<hash8>/); tests pick the one of this checkout's source verified last."""
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import xyf_build
from xy_forever import paths

SOURCE = {'target/a.cpp': '11', 'xyf_build.py': '22'}


def make(builds, data, passed=True, mode='xyf-product', source=SOURCE, name=None, when=0):
    digest = hashlib.sha256(data).hexdigest()
    d = builds / (name or digest[:8])
    d.mkdir()
    (d / 'candidate.ldr').write_bytes(data)
    (d / 'manifest.json').write_text(json.dumps({'image': digest[:8], 'candidate_sha256': digest, 'source_sha256': source}))
    (d / 'verification.json').write_text(json.dumps({'passed': passed, 'mode': mode}))
    os.utime(d / 'verification.json', (when, when))
    return d


class NewestVerifiedBuild(unittest.TestCase):
    def test_check_uses_its_own_build_after_shared_image_replacement(self):
        with tempfile.TemporaryDirectory() as tmp:
            shared, check = Path(tmp) / 'shared', Path(tmp) / 'check'
            shared.mkdir()
            check.mkdir()
            want = make(check, b'image')
            other = make(shared, b'image', source=dict(SOURCE, **{'target/a.cpp': '33'}))
            self.assertEqual(want.name, other.name)
            with mock.patch.object(paths, 'BUILDS', shared), mock.patch.object(xyf_build, 'source_hashes', lambda: SOURCE):
                with mock.patch.dict(os.environ, {paths.CHECK_TEST_BUILDS: str(check)}):
                    self.assertEqual(paths.newest_verified_build(), want)
                with mock.patch.dict(os.environ, {paths.CHECK_TEST_BUILDS: ''}):
                    self.assertIsNone(paths.newest_verified_build())

    def test_the_last_verified_of_this_source_counts(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {paths.CHECK_TEST_BUILDS: ''}):
            builds = Path(tmp)
            make(builds, b'a', when=1000)
            want = make(builds, b'b', when=2000)
            make(builds, b'c', passed=False, when=3000)
            make(builds, b'd', source=dict(SOURCE, **{'target/a.cpp': '33'}), when=4000)   # another branch's source
            make(builds, b'e', mode='xyf-mtp-usb', when=5000)
            make(builds, b'f', name='xy-forever-product-195-001', when=6000)   # the old numbered names
            make(builds, b'g', name='0badc0de', when=7000)            # not its image
            with mock.patch.object(paths, 'BUILDS', builds), mock.patch.object(xyf_build, 'source_hashes', lambda: SOURCE):
                self.assertEqual(paths.newest_verified_build(), want)
            with mock.patch.object(paths, 'BUILDS', builds), \
                    mock.patch.object(xyf_build, 'source_hashes', lambda: dict(SOURCE, **{'xyf_build.py': '44'})):
                self.assertIsNone(paths.newest_verified_build())     # this checkout's source changed: none counts

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
