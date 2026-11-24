"""scripts/web_canary.py: canary prereleases from main and the one the Pages deploy offers (#301)."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import web_canary

from xy_forever import release

A, B, C = ('a' * 40, 'b' * 40, 'c' * 40)
DATE = '2026-11-23T10:00:00Z'


def stable_of(data):
    return dict(web_canary.web_release.pin_of(data), notes='First preview.')


class FakeGitHub:
    """Releases as {tag: {name: bytes}}, newest first; main's history as an ordered list of commits."""

    def __init__(self, releases=(), history=(A, B, C), stable_at=A):
        self.releases = dict(releases)
        self.history = list(history)
        self.stable_at = stable_at
        self.created = []

    def canaries(self):
        return list(self.releases)

    def download(self, tag, name, folder):
        if name not in self.releases[tag]:
            raise OSError('no asset %s' % name)
        (Path(folder) / name).write_bytes(self.releases[tag][name])
        return Path(folder) / name

    def create(self, tag, commit, title, notes, files):
        self.created.append((tag, commit, title, {Path(f).name: Path(f).read_bytes() for f in files}))

    def contains(self, ancestor, commit):
        commit = {'HEAD': self.history[-1], 'v%s' % release.VERSION: self.stable_at}.get(commit, commit)
        return ancestor in self.history and self.history.index(ancestor) <= self.history.index(commit)

    def commit_date(self, commit):
        return DATE


def canary(commit, data, **change):
    pin = dict(web_canary.canary_pin(data, commit, DATE), **change)
    return {'os.ldr': data, 'release.json': json.dumps(pin).encode()}


class PublishTest(unittest.TestCase):
    def artifact(self, tmp, data, **change):
        manifest = {
            'mode': 'xyf-product',
            'version': release.VERSION,
            'candidate_sha256': hashlib.sha256(data).hexdigest(),
        }
        (Path(tmp) / 'os.ldr').write_bytes(data)
        (Path(tmp) / 'manifest.json').write_text(json.dumps(dict(manifest, **change)))
        return Path(tmp)

    def test_publishes_only_an_image_the_current_canary_lacks(self):
        with tempfile.TemporaryDirectory() as tmp:
            gh = FakeGitHub()
            pin, _ = web_canary.publish(self.artifact(tmp, b'first'), A, gh)
            [(tag, commit, title, files)] = gh.created
            self.assertEqual((tag, commit), ('canary-' + A, A))
            self.assertEqual(title, 'Canary aaaaaaa (2026-11-23)')
            self.assertEqual(files['os.ldr'], b'first')
            self.assertEqual(json.loads(files['release.json']), pin)
            self.assertEqual((pin['channel'], pin['commit'], pin['date']), ('canary', A, DATE))
            self.assertEqual(pin['sha256'], hashlib.sha256(b'first').hexdigest())

            gh = FakeGitHub({'canary-' + A: files})  # a docs-only or same-bytes change on main
            pin, said = web_canary.publish(self.artifact(tmp, b'first'), B, gh)
            self.assertIsNone(pin)
            self.assertIn('unchanged', said)
            self.assertEqual(gh.created, [])
            web_canary.publish(self.artifact(tmp, b'second'), B, gh)
            self.assertEqual([c[0] for c in gh.created], ['canary-' + B])

    def test_refuses_a_build_its_manifest_does_not_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            gh = FakeGitHub()
            for change in ({'candidate_sha256': '0' * 64}, {'mode': 'xyf-dev'}, {'version': '9.9.9'}):
                with self.subTest(change), self.assertRaises(ValueError):
                    web_canary.publish(self.artifact(tmp, b'first', **change), A, gh)
            self.assertEqual(gh.created, [])


class StageTest(unittest.TestCase):
    def stage(self, gh, stable=b'stable'):
        logged = []
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'canary'
            pin = web_canary.stage(stable_of(stable), gh, folder=folder, log=logged.append)
            staged = {p.name: p.read_bytes() for p in folder.iterdir()}
        return pin, staged, logged

    def test_offers_the_newest_canary_on_main_after_the_stable_release(self):
        gh = FakeGitHub({'canary-' + C: canary(C, b'third'), 'canary-' + B: canary(B, b'second')})
        pin, staged, _ = self.stage(gh)
        self.assertEqual(pin['commit'], C)
        self.assertEqual(staged['os.ldr'], b'third')
        self.assertEqual(json.loads(staged['release.json']), pin)

    def test_skips_canaries_that_are_off_main_or_do_not_match_their_pin(self):
        off_main = 'd' * 40
        gh = FakeGitHub({
            'canary-' + off_main: canary(off_main, b'other'),
            'canary-' + C: canary(C, b'third', bytes=1),
            'canary-' + B: canary(B, b'second'),
        })  # fmt: skip
        pin, staged, logged = self.stage(gh)
        self.assertEqual(pin['commit'], B)
        self.assertEqual(staged['os.ldr'], b'second')
        self.assertTrue(any('not on the deployed main' in line for line in logged))
        self.assertTrue(any('not the one its pin names' in line for line in logged))

    def test_a_tag_and_pin_that_disagree_are_skipped(self):
        gh = FakeGitHub({'canary-' + C: canary(B, b'third'), 'canary-' + B: {'os.ldr': b'second'}})
        self.assertEqual(self.stage(gh)[:2], (None, {}))

    def test_offers_none_when_stable_is_as_new(self):
        gh = FakeGitHub({'canary-' + B: canary(B, b'second')}, stable_at=B)
        self.assertEqual(self.stage(gh)[:2], (None, {}))
        gh = FakeGitHub({'canary-' + C: canary(C, b'stable')})  # the stable image itself
        self.assertEqual(self.stage(gh)[:2], (None, {}))
        self.assertEqual(self.stage(FakeGitHub())[:2], (None, {}))


if __name__ == '__main__':
    unittest.main()
