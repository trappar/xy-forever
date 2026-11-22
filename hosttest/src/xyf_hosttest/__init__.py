"""XY Forever's native code built for the host (#233, #229): `uv sync` builds it, incrementally (hosttest/CMakeLists.txt).

module(name): the nanobind submodule bind/NAME.cpp makes (hosttest/CMakeLists.txt lists each one's sources).
DOCTEST: the doctest binary with every C++ unit test (tests/test_doctest.py runs it).
program(name): a program or loadable library built on its own (xyf_program): a test's variant, a host tool, the
simulator.
observer, when set (tests/xyf_select.py), is told which repository C++ files a test used: observer(paths), each
relative to the repository root. It follows their #includes.
"""

import json
from pathlib import Path

DOCTEST = Path(__file__).with_name('doctest')
observer = None  # observer(paths): C++ files a test used (see above)
_programs = None


def used(*paths):
    """Tells the observer a test used these repository files (a glue file, a doctest case file)."""
    if observer:
        observer(paths)


def module(name):
    """The submodule bind/NAME.cpp binds."""
    from . import _native

    used('hosttest/bind/%s.cpp' % name)
    return getattr(_native, name)


def program(name):
    """The path of the program or library xyf_program(NAME) built."""
    global _programs
    if _programs is None:
        _programs = json.loads(Path(__file__).with_name('programs.json').read_text())
    entry = _programs[name]
    used(*entry['sources'])
    return Path(__file__).with_name('bin') / entry['file']
