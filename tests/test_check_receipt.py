"""scripts/check.py: the receipt key, the clean-tree and failed-step refusals, the version refusal and
--confirm, in a temporary git repository with stubbed steps (nothing is built)."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import pytest

SPEC = importlib.util.spec_from_file_location('check_script', Path(__file__).resolve().parents[1] / 'scripts/check.py')
C = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(C)

ENV = dict(os.environ, GIT_AUTHOR_NAME='test', GIT_AUTHOR_EMAIL='test@example.com', GIT_COMMITTER_NAME='test',
           GIT_COMMITTER_EMAIL='test@example.com', GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1')


def ok(name='ok'):
    return C.step(name, [sys.executable, '-c', 'pass'], '.')


def fails(name='bad'):
    return C.step(name, [sys.executable, '-c', 'raise SystemExit(3)'], '.')


@pytest.mark.gate
class CheckReceiptTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root, self.checks = Path(tmp.name) / 'repo', Path(tmp.name) / 'checks'
        self.log = Path(tmp.name) / 'check.log'
        self.root.mkdir()
        self.git('init', '-q', '-b', 'main')
        (self.root / '.gitignore').write_text('ignored.txt\n')
        self.commit('a.txt', 'one')

    def git(self, *args):
        return subprocess.run(['git', *args], cwd=self.root, env=ENV, check=True, capture_output=True,
                              text=True).stdout.strip()

    def commit(self, name, text):
        (self.root / name).parent.mkdir(parents=True, exist_ok=True)
        (self.root / name).write_text(text)
        self.git('add', '-A')
        self.git('commit', '-q', '-m', name)

    def run_check(self, *steps):
        with contextlib.redirect_stdout(io.StringIO()):
            return C.check(self.root, lambda tree: list(steps), self.checks, self.log)

    def confirm(self, ref='HEAD'):
        with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            status = C.main(['--confirm', ref], self.root, self.checks)
        return status, out.getvalue() + err.getvalue()

    def test_key_is_the_tree(self):
        record = self.run_check(ok())
        self.assertEqual(record['tree'], self.git('rev-parse', 'HEAD^{tree}'))
        self.assertTrue((self.checks / (record['tree'] + '.json')).is_file())
        self.assertEqual(record['version'], C.VERSION)
        self.assertEqual([s['result'] for s in record['steps']], ['pass'])
        self.git('commit', '-q', '--allow-empty', '-m', 'same files')     # another commit, the same tree
        self.assertEqual(self.confirm()[0], 0)

    def test_dirty_tree_refused(self):
        (self.root / 'ignored.txt').write_text('gitignored files do not count')
        (self.root / 'new.txt').write_text('untracked')
        with self.assertRaisesRegex(C.CheckError, 'not clean at start'):
            self.run_check(ok())
        (self.root / 'new.txt').unlink()
        (self.root / 'a.txt').write_text('changed')
        with self.assertRaisesRegex(C.CheckError, 'not clean at start'):
            self.run_check(ok())
        self.assertFalse(self.checks.exists())

    def test_tree_dirtied_during_run_refused(self):
        dirty = C.step('dirty', [sys.executable, '-c', 'open("x.txt", "w").write("x")'], self.root)
        with self.assertRaisesRegex(C.CheckError, 'not clean at end'):
            self.run_check(dirty)
        self.assertFalse(self.checks.exists())

    def test_failed_step_writes_no_receipt(self):
        tree = self.git('rev-parse', 'HEAD^{tree}')
        self.run_check(ok())
        record = self.run_check(ok(), fails(), ok('never run'))
        self.assertFalse(record['passed'])
        self.assertEqual([s['result'] for s in record['steps']], ['pass', 'fail'])
        self.assertFalse((self.checks / (tree + '.json')).exists())        # the older pass is gone too
        status, text = self.confirm()
        self.assertEqual(status, 1)
        self.assertIn('failed at bad', text)

    def test_newer_version_refused(self):
        record = self.run_check(ok())
        path = self.checks / (record['tree'] + '.json')
        path.write_text(json.dumps(dict(record, version=C.VERSION + 1)))
        status, text = self.confirm()
        self.assertEqual(status, 1)
        self.assertIn('newer than this check.py reads', text)

    def test_confirm_no_receipt(self):
        status, text = self.confirm()
        self.assertEqual(status, 1)
        self.assertIn('no receipt for HEAD', text)

    def test_confirm_failed_receipt(self):
        record = self.run_check(ok())
        path = self.checks / (record['tree'] + '.json')
        path.write_text(json.dumps(dict(record, passed=False)))
        status, text = self.confirm()
        self.assertEqual(status, 1)
        self.assertIn('failed receipt', text)

    def test_confirm_tree_differs(self):
        self.git('switch', '-q', '-c', '7-topic')
        self.commit('b.txt', 'two')
        self.run_check(ok())
        self.assertEqual(self.confirm('7-topic')[0], 0)
        self.git('switch', '-q', 'main')
        self.commit('c.txt', 'newer main')
        self.git('switch', '-q', '7-topic')
        self.git('rebase', '-q', 'main')                    # the same change on a newer main: another tree
        status, text = self.confirm('7-topic')
        self.assertEqual(status, 1)
        self.assertIn('tree differs', text)

    def test_confirm_prints_the_record(self):
        facts = C.step('facts', [sys.executable, '-c', 'pass'], '.', parse=lambda status, text: (True, dict(
            tests=dict(fast=dict(passed=12, skipped=1)),
            candidate=dict(sha256='ab' * 32, bytes=1234, build='/private/builds/check-x'))))
        self.run_check(facts)
        status, text = self.confirm()
        self.assertEqual(status, 0)
        self.assertIn('fast tier: 12 passed, 1 skipped', text)
        self.assertIn('candidate: image abababab, sha256 ' + 'ab' * 32 + ', 1234 bytes', text)

    def test_merge_base_prefers_origin_main(self):
        stale = self.git('rev-parse', 'HEAD')
        self.commit('clients/ts/a.ts', 'merged on main')
        self.git('update-ref', 'refs/remotes/origin/main', 'HEAD')
        self.git('switch', '-q', '-c', '7-topic')
        self.git('branch', '-q', '-f', 'main', stale)       # local main behind origin/main
        self.commit('b.txt', 'two')
        self.assertEqual(C.merge_base(self.root), self.git('rev-parse', 'origin/main'))
        self.assertFalse(C.changed(self.root, 'clients/ts'))
        self.assertFalse(C.changed(self.root, 'web'))
        self.git('update-ref', '-d', 'refs/remotes/origin/main')
        self.assertEqual(C.merge_base(self.root), stale)    # no origin/main: the local main
        self.assertTrue(C.changed(self.root, 'clients/ts'))

    def test_js_needs_node_modules(self):
        args = C.argparse.Namespace(js=True)
        self.assertEqual(C.js_steps(args, self.root), [])  # no clients/ts: nothing to test
        (self.root / 'clients/ts').mkdir(parents=True)
        with self.assertRaisesRegex(C.CheckError, 'clients/ts/node_modules, which is missing: run `pnpm install`'):
            C.js_steps(args, self.root)
        (self.root / 'clients/ts/node_modules').mkdir()
        self.assertEqual([s['name'] for s in C.js_steps(args, self.root)], ['pnpm run typecheck (clients/ts)'])
        self.assertEqual(C.js_steps(C.argparse.Namespace(js=False), self.root), [])   # unchanged on main
        (self.root / 'web').mkdir()
        with self.assertRaisesRegex(C.CheckError, 'web/node_modules, which is missing: run `pnpm install`'):
            C.js_steps(args, self.root)
        (self.root / 'web/node_modules').mkdir()
        steps = C.js_steps(args, self.root)
        self.assertEqual([s['name'] for s in steps], ['pnpm run typecheck (clients/ts)', 'pnpm run typecheck (web)'])
        self.assertEqual([s['cmd'][1:] for s in steps], [['run', 'typecheck']] * 2)
        self.assertEqual(steps[-1]['cwd'], self.root / 'web')

    def test_web_steps_follow_web_and_clients(self):
        for rel in ('clients/ts/node_modules', 'web/node_modules'):
            (self.root / rel).mkdir(parents=True)
        self.git('switch', '-q', '-c', '8-topic')
        names = lambda: [s['name'] for s in C.js_steps(C.argparse.Namespace(js=False), self.root)]  # noqa: E731
        self.assertEqual(names(), [])
        self.commit('web/a.ts', 'web only')
        self.assertEqual(names(), ['pnpm run typecheck (web)'])
        self.commit('clients/ts/a.ts', 'the client web uses')
        self.assertEqual(names(), ['pnpm run typecheck (clients/ts)', 'pnpm run typecheck (web)'])

    def test_failing_web_step_fails_the_check(self):
        tsc = C.step('pnpm run typecheck (web)', [sys.executable, '-c', 'raise SystemExit(2)'], '.')
        results, _ = C.run_steps([ok(), tsc, ok('after')], self.log)
        self.assertEqual([(r['name'], r['result']) for r in results], [('ok', 'pass'), ('pnpm run typecheck (web)', 'fail')])

    def test_budgets_name_the_step_and_the_total(self):
        self.assertEqual(C.over_budget([dict(name='a', seconds=10.0), dict(name='b', seconds=12.0)]), [])
        self.assertEqual(C.over_budget([dict(name='a', seconds=16.0), dict(name='b', seconds=15.0)]),
                         ['a took 16.0 s: over the 15 s a step', 'the check took 31.0 s: over 30 s'])

    def test_verify_parser(self):
        with tempfile.TemporaryDirectory() as tmp:
            builds = Path(tmp)
            out = builds / 'check-123'
            out.mkdir()
            (out / 'candidate.ldr').write_bytes(b'candidate')
            (out / 'verification.json').write_text(json.dumps(dict(passed=True, mode='xyf-product')))
            digest = __import__('hashlib').sha256(b'candidate').hexdigest()
            test_builds = builds / 'test-builds'
            parse = C.verify_parser(out, 't' * 40, 'c' * 40, test_builds)
            (out / 'manifest.json').write_text(json.dumps(dict(candidate_sha256='0' * 64, image='00000000')))
            self.assertFalse(parse(0, '{\n  "passed": true\n}\n')[0])      # bytes differ from the manifest
            (out / 'manifest.json').write_text(json.dumps(dict(candidate_sha256=digest, image=digest[:8])))
            self.assertFalse(parse(0, '{\n  "passed": false\n}\n')[0])
            self.assertFalse(parse(1, '{\n  "passed": true\n}\n')[0])
            with mock.patch.object(C.paths, 'BUILDS', builds):
                passed, facts = parse(0, 'log line\n{\n  "passed": true\n}\n')
            self.assertTrue(passed)
            named = builds / digest[:8]                          # copied to builds/<image>
            self.assertEqual(facts['candidate'], dict(sha256=digest, image=digest[:8], bytes=9, build=str(named)))
            self.assertEqual(json.loads((named / 'check.json').read_text()),
                             dict(image=digest[:8], tree='t' * 40, commit='c' * 40))
            self.assertEqual((out / 'candidate.ldr').read_bytes(), b'candidate')   # the cache's output stays as built
            self.assertFalse((out / 'check.json').exists())
            test_build = test_builds / named.name
            self.assertEqual(C.paths.build_image(test_build), C.paths.build_image(named))
            self.assertEqual((test_build / 'candidate.ldr').read_bytes(), b'candidate')
            self.assertEqual((test_build / 'check.json').read_bytes(), (named / 'check.json').read_bytes())

if __name__ == '__main__':
    unittest.main()
