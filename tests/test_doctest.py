"""The C++ unit tests (tests/native/*_test.cpp, doctest): a test class per file, running that file's cases in the
doctest binary `uv sync` built (hosttest/). A file whose first line is `// tier: gate` is in the gate tier."""
from pathlib import Path
import subprocess
import unittest

import pytest

import xyf_hosttest

ROOT = Path(__file__).resolve().parents[1]


def cases(rel):
    def test_cases(self):
        xyf_hosttest.used(rel)
        run = subprocess.run([str(xyf_hosttest.DOCTEST), '--source-file=*' + rel, '--no-intro', '--no-version'],
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertNotIn('test cases: 0 |', run.stdout, 'no cases ran for ' + rel)
    return test_cases


for path in sorted(ROOT.glob('tests/native/*_test.cpp')):
    name = 'Test_' + path.stem
    test = type(name, (unittest.TestCase,), {'test_cases': cases(path.relative_to(ROOT).as_posix())})
    globals()[name] = pytest.mark.gate(test) if path.read_text().startswith('// tier: gate') else test
