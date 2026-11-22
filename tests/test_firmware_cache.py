"""turbo.json: the firmware build's cache inputs are exactly the sources xyf_build.py hashes (own_sources), and the
cache stays local (scripts/firmware_cache.py; the hit, miss and failure cases: scripts/firmware_cache_proof.py)."""
import json
from pathlib import Path
import unittest

import xyf_build

ROOT = Path(__file__).resolve().parents[1]
TURBO = json.loads((ROOT / 'turbo.json').read_text())
TASKS = ('//#firmware-build', '//#firmware-verify')
WRAPPER = 'scripts/firmware_cache.py'          # the task commands: an input too


class FirmwareCache(unittest.TestCase):
    def test_inputs_are_the_build_sources(self):
        sources = set(xyf_build.own_sources())
        for name in TASKS:
            task = TURBO['tasks'][name]
            matched = {str(p.relative_to(ROOT)) for g in task['inputs'] for p in ROOT.glob(g) if p.is_file()}
            self.assertEqual(matched - {WRAPPER}, sources, name)
            self.assertIn(WRAPPER, task['inputs'])
            self.assertEqual(task['env'], ['XYF_BUILD_KEY'], name)

    def test_local_only(self):
        self.assertIs(TURBO['remoteCache']['enabled'], False)
        self.assertEqual(TURBO['cacheDir'], '.turbo/cache')       # this checkout's, not shared across worktrees
        self.assertEqual(TURBO['tasks']['//#firmware-build']['outputs'], ['build/product/**'])
        ignored = (ROOT / '.gitignore').read_text().splitlines()
        self.assertIn('.turbo/', ignored)
        self.assertIn('/build/', ignored)


if __name__ == '__main__':
    unittest.main()
