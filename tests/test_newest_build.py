"""Tests pick only a verified build of this checkout's own build number, never another branch's newer one."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from xy_forever import paths


def make(builds, name, passed=True, mode='xyf-product'):
    d = builds / name
    d.mkdir()
    (d / 'candidate.ldr').write_bytes(b'x')
    (d / 'verification.json').write_text(json.dumps({'passed': passed, 'mode': mode}))
    return d


class NewestVerifiedBuild(unittest.TestCase):
    def test_source_number_is_read_from_modes(self):
        self.assertIsInstance(paths.source_build_number(), int)

    def test_only_this_source_number_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            builds = Path(tmp)
            make(builds, 'xy-forever-product-193-001')
            want = make(builds, 'xy-forever-product-193-002')
            make(builds, 'xy-forever-product-193-003', passed=False)
            make(builds, 'xy-forever-product-195-001')          # another branch's newer build
            with mock.patch.object(paths, 'BUILDS', builds), \
                    mock.patch.object(paths, 'source_build_number', return_value=193):
                self.assertEqual(paths.newest_verified_build(), want)
            with mock.patch.object(paths, 'BUILDS', builds), \
                    mock.patch.object(paths, 'source_build_number', return_value=194):
                self.assertIsNone(paths.newest_verified_build())


if __name__ == '__main__':
    unittest.main()
