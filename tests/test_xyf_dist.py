"""The developer package's contents and versions (scripts/xyf_dist.py, pypi/pyproject.toml; #284). Its modules are
the ones listed, every xy_forever import they make at load time stays inside the list, the repository lints exactly
those as Python 3.11, and the staged folder holds the inputs and nothing else. The build, the install on Python 3.11
and the run against a stand-in OP-XY are the PyPI workflow's (.github/workflows/pypi.yml), not the suite's."""

import ast
import json
import tempfile
import tomllib
import unittest
from pathlib import Path

import pytest
import xyf_dist as D

ROOT = Path(__file__).resolve().parents[1]
# Imports of a module outside the package, each inside a function no xyf command reaches.
LAZY = {
    ('modsend', 'modindex'): 'xyf-host mods fetch',
    ('paths', 'xyf_build'): 'paths.verified_build',
}


def imports(name):
    """[(imported module, at load time)] of xy_forever/NAME.py: its xy_forever imports, relative or absolute."""
    tree = ast.parse((ROOT / 'xy_forever' / (name + '.py')).read_text())
    top = {id(node) for node in tree.body}
    found = []
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.ImportFrom):
            package = node.module or ''
            if node.level == 1 or package.startswith('xy_forever'):
                base = package.removeprefix('xy_forever').lstrip('.')
                names = [base] if base else [a.name for a in node.names]
        elif isinstance(node, ast.Import):
            names = [a.name.split('.')[1] for a in node.names if a.name.startswith('xy_forever.')]
        found += [(n.split('.')[0], id(node) in top) for n in names]
    return found


@pytest.mark.uses('scripts/xyf_dist.py')
class ContentsTest(unittest.TestCase):
    def test_the_modules_and_their_imports(self):
        modules, outside = set(D.modules()), set()
        for name in sorted(modules):
            for target, at_load in imports(name):
                if target in modules:
                    continue
                outside.add((name, target))
                self.assertFalse(at_load, '%s imports %s at load time: list it in pypi/pyproject.toml' % (name, target))
                self.assertIn(
                    (name, target),
                    LAZY,
                    '%s imports %s: list it, or say why no xyf command reaches it' % (name, target),
                )
        self.assertEqual(outside, set(LAZY))  # none stale

    def test_the_repository_lints_them_as_python_3_11(self):
        ruff = tomllib.loads((ROOT / 'pyproject.toml').read_text())['tool']['ruff']['per-file-target-version']
        self.assertEqual(ruff, {'xy_forever/%s.py' % m: 'py311' for m in D.modules()})

    def test_the_staged_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = D.stage(Path(tmp) / 'pkg', '0.2.0.dev7')
            files = sorted(str(p.relative_to(out)) for p in out.rglob('*') if p.is_file())
            self.assertEqual(
                files,
                sorted(['pyproject.toml', 'README.md', 'LICENSE'] + ['xy_forever/%s.py' % m for m in D.modules()]),
            )
            self.assertIn('\nversion = "0.2.0.dev7"', (out / 'pyproject.toml').read_text())
        for path in D.inputs():
            self.assertTrue((ROOT / path).exists(), path)

    def test_the_facts_it_carries(self):
        """dev_entry's stock OS 1.2.1 pin, the boot's staging size and the cold policy are the repository's."""
        import re

        from xy_forever import dev_entry, xyf_build

        facts = json.loads((ROOT / 'stock_facts.json').read_text())
        found = [v for v in _walk(facts) if v.get('sha256') == dev_entry.STOCK_SHA256]
        self.assertEqual([v['bytes'] for v in found], [dev_entry.STOCK_BYTES])
        staging = re.search(r'staging_bytes = (0x[0-9a-f]+)u', (ROOT / 'target/file_boot.hpp').read_text())
        self.assertEqual(dev_entry.STAGING_BYTES, int(staging.group(1), 16))
        cold = (ROOT / 'target/cold.cpp').read_text().replace('XYF_STACK_END', '%#xu' % xyf_build.WIDE_STACK_END)
        for name, ranges in (('allowed', dev_entry.ALLOWED), ('protected_ranges', dev_entry.PROTECTED)):
            body = cold[cold.index('const Range %s[] = {' % name) :]
            body = body[: body.index('};')]
            found = [(int(a, 16), int(b, 16)) for a, b in re.findall(r'\{(0x[0-9a-fA-F]+)u, (0x[0-9a-fA-F]+)u\}', body)]
            self.assertEqual(tuple(found), ranges, name)


def _walk(value):
    """Every dict inside value."""
    if isinstance(value, dict):
        yield value
        for v in value.values():
            yield from _walk(v)
    elif isinstance(value, list):
        for v in value:
            yield from _walk(v)


class VersionTest(unittest.TestCase):
    def test_next_normal_version(self):
        self.assertEqual(D.next_version((0, 1, 0), []), (0, 1, 0))  # nothing published: the floor
        self.assertEqual(D.next_version((0, 1, 0), [(0, 1, 0)]), (0, 1, 1))  # published: the patch after it
        self.assertEqual(D.next_version((0, 2, 0), [(0, 1, 0), (0, 1, 3)]), (0, 2, 0))  # a raised floor wins
        self.assertEqual(D.parse('0.1.0'), (0, 1, 0))
        self.assertIsNone(D.parse('0.1.0rc1'))

    def test_an_unknown_base_counts_as_changed(self):
        self.assertTrue(D.changed(None))
        self.assertTrue(D.changed(D.ZERO))


if __name__ == '__main__':
    unittest.main()
