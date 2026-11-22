"""--testmon for the files Python coverage cannot see: C++ compiled by a test, scripts it runs as a subprocess.

testmon selects a test when Python it executed changed. A test also depends on:
  - the C++ sources and headers of every host build it used (xy_forever/hostbuild.py records them with
    clang -M; its observer reports them here, cache hits included);
  - the C++ of the hosttest modules and doctest files it used (xyf_hosttest.observer: a bind/NAME.cpp or a
    *_test.cpp), with what they include and, for each header, the .cpp beside it (includes());
  - the files named by its `uses` marker: @pytest.mark.uses('scripts/x.py', 'sim/') (a folder: every
    file in it). Tests of the simulator tier use sim/ (its build caches itself, outside hostbuild).
Each run records, per test that ran, those files' hashes (pytest's cache, key xyf/deps). With --testmon,
a test whose recorded files changed is taken out of testmon's deselection, so it runs, with the rest of its
class (a setUpClass compile is recorded by the class's first test).
"""

import hashlib
import os
import re
from pathlib import Path

import pytest
import xyf_hosttest

from xy_forever import hostbuild

ROOT = Path(__file__).resolve().parents[1]
KEY = 'xyf/deps'
_hashes = {}
_includes = {}
INCLUDE = re.compile(rb'^\s*#\s*include\s*"([^"]+)"', re.M)
SEARCH = ('', 'native', 'target', 'third_party/doctest')  # hosttest/CMakeLists.txt's include folders


def includes(rel):
    """rel (a repository path) and the repository files it includes, transitively, each header with the .cpp
    beside it (its implementation, linked into the same binary)."""
    if rel in _includes:
        return _includes[rel]
    seen, todo = set(), [rel]
    while todo:
        name = todo.pop()
        if name in seen or not (ROOT / name).is_file():
            continue
        seen.add(name)
        stem, ext = os.path.splitext(name)
        if ext in ('.hpp', '.h') and (ROOT / (stem + '.cpp')).is_file():
            todo.append(stem + '.cpp')
        here = os.path.dirname(name)
        for found in INCLUDE.findall((ROOT / name).read_bytes()):
            for base in (here, *SEARCH):
                candidate = os.path.normpath(os.path.join(base, found.decode()))
                if (ROOT / candidate).is_file():
                    todo.append(candidate)
                    break
    _includes[rel] = seen
    return seen


def digest(rel):
    """A file's (or a folder's files') content hash, '' when gone."""
    path = ROOT / rel
    if rel not in _hashes:
        files = sorted(p for p in path.rglob('*') if p.is_file()) if path.is_dir() else [path]
        h = hashlib.sha256()
        for f in files:
            try:
                h.update(str(f.relative_to(ROOT)).encode() + b'\0' + f.read_bytes())
            except OSError:
                pass
        _hashes[rel] = h.hexdigest() if any(f.exists() for f in files) else ''
    return _hashes[rel]


class Recorder:
    """Worker side (or the only process): the files each test used, in its report's user_properties."""

    def __init__(self):
        self.files, self.compile_s, self.compile_cpu_s = set(), 0.0, 0.0
        hostbuild.observer = self.compiled
        xyf_hosttest.observer = self.used

    def used(self, paths):
        for rel in paths:
            self.files.update(includes(rel))

    def compiled(self, files, seconds, cpu_s):
        for f in files:
            if f.startswith(str(ROOT) + os.sep) and os.path.isfile(f):
                self.files.add(os.path.relpath(f, ROOT))
        self.compile_s += seconds
        self.compile_cpu_s += cpu_s

    @pytest.hookimpl(tryfirst=True)
    def pytest_runtest_setup(self, item):
        self.files, self.compile_s = set(), 0.0
        for mark in item.iter_markers('uses'):
            self.files.update(str(p).rstrip('/') for p in mark.args)

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_makereport(self, item, call):
        report = yield
        if call.when == 'teardown':
            report.user_properties.append(('xyf_deps', {f: digest(f) for f in sorted(self.files)}))
        report.user_properties += [('xyf_compile_s', self.compile_s), ('xyf_compile_cpu_s', self.compile_cpu_s)]
        self.compile_s = self.compile_cpu_s = 0.0  # charged to this phase, then reset
        return report


class Selector:
    """Controller side: stores the recorded files and, with --testmon, keeps tests whose files changed."""

    def __init__(self, config):
        self.config, self.recorded = config, config.cache.get(KEY, {}) if config.cache else {}

    @pytest.hookimpl(tryfirst=True)
    def pytest_sessionstart(self, session):
        select = self.config.pluginmanager.get_plugin('TestmonSelect')
        if select is None:
            return
        changed = {test for test, files in self.recorded.items() if any(digest(f) != h for f, h in files.items())}
        classes = tuple(t.rsplit('::', 1)[0] + '::' for t in changed)  # a class fixture's compile: its first test
        select.deselected_tests = [t for t in select.deselected_tests if not t.startswith(classes)]
        homes = {t.split('::')[0] for t in changed}
        select.deselected_files = [f for f in select.deselected_files if f not in homes]

    def pytest_runtest_logreport(self, report):
        for name, value in report.user_properties:
            if name == 'xyf_deps' and report.passed:
                self.recorded[report.nodeid] = value

    def pytest_sessionfinish(self, session):
        if self.config.cache:
            self.config.cache.set(KEY, self.recorded)


def register(config):
    config.addinivalue_line('markers', 'uses(*paths): files (or folders) the test runs without importing them')
    if not hasattr(config, 'workerinput'):
        config.pluginmanager.register(Selector(config), 'xyf-select')
    config.pluginmanager.register(Recorder(), 'xyf-record')
