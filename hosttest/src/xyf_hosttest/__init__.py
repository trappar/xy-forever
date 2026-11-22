"""XY Forever's native code built for the host, for the tests (#233): `uv sync` builds it once.

module(name): the nanobind submodule bind/NAME.cpp makes (hosttest/CMakeLists.txt lists each one's sources).
DOCTEST: the doctest binary with every C++ unit test (tests/test_doctest.py runs it).
observer, when set (tests/xyf_select.py), is told which repository C++ files a test used: observer(paths), each
relative to the repository root. It follows their #includes.
"""
from pathlib import Path

DOCTEST = Path(__file__).with_name('doctest')
observer = None    # observer(paths): C++ files a test used (see above)


def used(*paths):
    """Tells the observer a test used these repository files (a glue file, a doctest case file)."""
    if observer:
        observer(paths)


def module(name):
    """The submodule bind/NAME.cpp binds."""
    from . import _native
    used('hosttest/bind/%s.cpp' % name)
    return getattr(_native, name)
