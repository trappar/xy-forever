"""scripts/web_release.py: the release pin and the staged image for the web app (#178)."""

import json
import tempfile
import unittest
from pathlib import Path

import web_release
from conftest import verified_build


class StageTest(unittest.TestCase):
    def test_stage_pins_and_copies_a_verified_build(self):
        build = verified_build(self)
        with tempfile.TemporaryDirectory() as tmp:
            pin_path, staged = Path(tmp) / 'release.json', Path(tmp) / 'public/release/os.ldr'
            pin_path.write_text('{"schema": 1, "version": "", "image": "", "sha256": "", "bytes": 0}')
            with self.assertRaisesRegex(ValueError, 'give --notes'):
                web_release.stage(build, pin_path, staged)
            self.assertFalse(staged.exists())
            pin = web_release.stage(build, pin_path, staged, notes='  Faster updates.  ')
            self.assertEqual(pin['notes'], 'Faster updates.')
            self.assertEqual(web_release.stage(build, pin_path, staged), pin)
            self.assertEqual(json.loads(pin_path.read_text()), pin)
            self.assertEqual(staged.read_bytes(), (build / 'candidate.ldr').read_bytes())
            self.assertIsNone(web_release.check(pin_path, staged))
            pin_path.write_text(json.dumps(dict(pin, bytes=pin['bytes'] + 1)))
            self.assertIn('incorrect', web_release.check(pin_path, staged))
            pin_path.write_text(json.dumps(pin))
            staged.write_bytes(b'other')
            self.assertIn('not the pinned', web_release.check(pin_path, staged))
            with self.assertRaisesRegex(ValueError, 'not a verified product build'):
                web_release.stage(Path(tmp), pin_path, staged)


if __name__ == '__main__':
    unittest.main()
