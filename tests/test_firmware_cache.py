"""turbo.json: the firmware build's cache inputs are exactly the sources xyf_build.py hashes (own_sources), and the
cache stays local (scripts/firmware_cache.py; the hit, miss and failure cases: scripts/firmware_cache_proof.py)."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import firmware_cache

from xy_forever import xyf_build

ROOT = Path(__file__).resolve().parents[1]
TURBO = json.loads((ROOT / 'turbo.json').read_text())
BUILD, VERIFY = '//#firmware-build', '//#firmware-verify'
WRAPPER = 'scripts/firmware_cache.py'  # the task commands: an input too


class FirmwareCache(unittest.TestCase):
    def test_inputs_are_the_build_sources(self):
        task = TURBO['tasks'][BUILD]
        matched = {str(p.relative_to(ROOT)) for g in task['inputs'] for p in ROOT.glob(g) if p.is_file()}
        self.assertEqual(matched - {WRAPPER}, set(xyf_build.own_sources()))
        self.assertIn(WRAPPER, task['inputs'])
        self.assertEqual(TURBO['globalEnv'], ['XYF_BUILD_KEY'])

    def test_verify_keys_on_the_build(self):
        # Turborepo hashes a task's dependencies into its key: verify needs no source list of its own.
        verify = TURBO['tasks'][VERIFY]
        self.assertEqual(verify['dependsOn'], [BUILD])
        self.assertEqual(verify['inputs'], [WRAPPER])  # never empty: [] means every file

    def test_local_only(self):
        self.assertIs(TURBO['remoteCache']['enabled'], False)
        self.assertEqual(TURBO['cacheDir'], '.turbo/cache')  # this checkout's, not shared across worktrees
        self.assertEqual(TURBO['tasks'][BUILD]['outputs'], ['build/product/**'])
        ignored = (ROOT / '.gitignore').read_text().splitlines()
        self.assertIn('.turbo/', ignored)
        self.assertIn('/build/', ignored)

    def test_a_run_prints_one_line_saying_where_the_output_went(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(firmware_cache, 'OUT', Path(tmp)):
            (Path(tmp) / 'manifest.json').write_text(json.dumps({'image': 'bfe69f9c'}))
            hit = 'Tasks:    1 successful, 1 total\nCached:    1 cached, 1 total\n'
            line = firmware_cache.summary('build', 0, hit)
            self.assertRegex(line, r'^firmware build: .+ \(image bfe69f9c, from the cache\)$')
            self.assertIn(tmp.split('/')[-1], line)
            self.assertTrue(firmware_cache.summary('verify', 0, 'Cached:    1 cached, 2 total').endswith(', built)'))
            self.assertEqual(firmware_cache.summary('build', 1, ''), 'firmware build: FAILED (exit 1; the log above)')


if __name__ == '__main__':
    unittest.main()
