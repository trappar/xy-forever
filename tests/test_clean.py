"""scripts/clean.py on a synthetic private folder: it keeps the newest N sessions and builds, never the protected
build, a build named in a status file, the newest verified build of each checkout, or anything recent, and deletes
only with --yes."""

import contextlib
import importlib.util
import io
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('xyf_clean', ROOT / 'scripts/clean.py')
C = importlib.util.module_from_spec(spec)
spec.loader.exec_module(C)


class Clean(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.xyf, self.status = Path(tmp.name) / 'xy-forever', Path(tmp.name) / 'status'
        self.status.mkdir()
        (self.status / 'README.md').write_text('Installed: builds/0badf00d (known good).\n')
        old = time.time() - 30 * 86400
        for folder, names in (
            ('sessions', ['s%d' % n for n in range(5)]),
            ('builds', ['b%d' % n for n in range(4)] + ['2a96c724', '0badf00d']),
        ):
            for n, name in enumerate(names):
                entry = self.xyf / folder / name
                entry.mkdir(parents=True)
                (entry / 'f').write_bytes(b'x' * 10)
                for p in (entry / 'f', entry):
                    os.utime(p, (old + n * 3600, old + n * 3600))  # later names are newer
        (self.xyf / 'recovery').mkdir()

    def clean(self, *argv, trees=()):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            C.main(list(argv), xyf=self.xyf, status=self.status, trees=list(trees))
        return out.getvalue()

    def checkout(self, name, text):
        """A synthetic checkout with one source file, and a verified build folder of those sources."""
        tree = self.xyf.parent / name
        for d in ('target', 'native', 'xy_forever', 'third_party'):
            (tree / d).mkdir(parents=True)
        (tree / 'target/a.cpp').write_text(text)
        (tree / 'toolchains.json').write_text('{}')
        (tree / 'stock_facts.json').write_text('{}')
        return tree

    def verified_build(self, name, tree, age):
        d = self.xyf / 'builds' / name
        d.mkdir()
        (d / 'candidate.ldr').write_bytes(b'c')
        (d / 'verification.json').write_text(json.dumps({'passed': True, 'mode': 'xyf-product'}))
        manifest = dict(image=name, candidate_sha256=name + '00', source_sha256=C.source_hashes(tree))
        (d / 'manifest.json').write_text(json.dumps(manifest))
        for p in (*d.iterdir(), d):
            os.utime(p, (time.time() - age, time.time() - age))

    def names(self, folder):
        return sorted(p.name for p in (self.xyf / folder).iterdir())

    def test_dry_run_changes_nothing(self):
        before = (self.names('sessions'), self.names('builds'))
        out = self.clean('--keep', '2')
        self.assertEqual((self.names('sessions'), self.names('builds')), before)
        self.assertIn('would remove 3', out)
        self.assertIn('dry run: nothing changed', out)

    def test_keeps_the_newest_and_the_protected(self):
        os.utime(self.xyf / 'sessions/s0', None)  # touched just now: in use, the newest
        self.clean('--keep', '2', '--yes')
        self.assertEqual(self.names('sessions'), ['s0', 's4'])
        self.assertEqual(self.names('builds'), ['0badf00d', '2a96c724', 'b2', 'b3'])  # protected: no place taken
        self.assertTrue((self.xyf / 'recovery').is_dir())

    def test_keeps_the_newest_verified_build_of_each_checkout(self):
        one, two = self.checkout('one', 'int a;'), self.checkout('two', 'int b;')
        self.verified_build('11111111', one, 40 * 86400)  # one's older build of the same sources: goes
        self.verified_build('22222222', one, 39 * 86400)
        self.verified_build('33333333', two, 38 * 86400)
        self.clean('--keep', '1', '--yes', trees=[one, two])
        self.assertEqual(self.names('builds'), ['0badf00d', '22222222', '2a96c724', '33333333', 'b3'])

    def test_the_automatic_run_prunes_builds_only(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            C.prune_builds(xyf=self.xyf, status=self.status, keep=1, trees=[])
        self.assertEqual(self.names('builds'), ['0badf00d', '2a96c724', 'b3'])
        self.assertEqual(len(self.names('sessions')), 5)
        self.assertEqual(out.getvalue().count('\n'), 1)  # one line: no listing in check's output

    def test_a_link_is_removed_as_a_link(self):
        target = self.xyf.parent / 'elsewhere'
        target.mkdir()
        link = self.xyf / 'sessions/link'
        link.symlink_to(target)
        os.utime(link, (1, 1), follow_symlinks=False)
        self.clean('--keep', '5', '--yes')
        self.assertFalse(link.is_symlink())
        self.assertTrue(target.is_dir())


if __name__ == '__main__':
    unittest.main()
