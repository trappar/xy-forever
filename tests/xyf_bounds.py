"""Hard bounds on every test (#230, xy_forever/bounds.py), as a pytest plugin over pytest-timeout.

Each test, with the fixtures before it, is bounded by the ini `timeout` (bounds.TEST_S), a simulator core
test by bounds.SIM_TEST_S. Past it, a thread prints HUNG: <test id> and every
thread's traceback, then ends the process: under xdist each worker leads its own process group, and the
whole group is killed (a child the test started with it); xdist names the test whose worker crashed. A
worker whose controller is gone ends with its group too (procgroup.end_group_with_parent). Children
started through xy_forever/procgroup lead groups of their own and end with their caller.

Loaded by tests/conftest.py, and with `-p xyf_bounds` by a test that runs pytest on a planted hang.
"""

import faulthandler
import os
import signal
import sys
import threading

import pytest

from xy_forever import bounds, procgroup


def pytest_configure(config):
    if os.environ.get('PYTEST_XDIST_WORKER') and os.getpgrp() != os.getpid():
        os.setpgid(0, 0)  # this worker leads a group: a hang ends it with its children
        procgroup.end_group_with_parent()


def pytest_collection_modifyitems(items):
    for item in items:
        if next((m.name for m in item.iter_markers() if m.name in ('gate', 'sim')), None) == 'sim':  # closest
            item.add_marker(pytest.mark.timeout(bounds.SIM_TEST_S))


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_protocol(item):
    """A test that uses the simulator (sim/) gets its build (the default one) before its bound: a cold build is a
    compile, not the test."""
    if any('sim/' in m.args for m in item.iter_markers('uses')):
        from xy_forever import sim

        if not sim.available():
            sim.build()
    yield


def _hung(item, seconds):
    nodeid, capture = item.nodeid, item.config.pluginmanager.getplugin('capturemanager')
    if capture:
        capture.suspend_global_capture(in_=True)  # to the real stderr (xdist: the controller's terminal)
        out, err = capture.read_global_capture()
        sys.stderr.write(out + err)
    sys.stderr.write(
        '\nHUNG: %s ran past its %g s bound; every thread, then its process group ends:\n' % (nodeid, seconds)
    )
    sys.stderr.flush()
    faulthandler.dump_traceback(file=sys.stderr, all_threads=True)
    sys.stderr.flush()
    if os.getpgrp() == os.getpid():
        os.killpg(0, signal.SIGKILL)
    os._exit(1)


@pytest.hookimpl(tryfirst=True)
def pytest_timeout_set_timer(item, settings):
    if settings.timeout > 0:
        item.xyf_bound = threading.Timer(settings.timeout, _hung, (item, settings.timeout))
        item.xyf_bound.daemon = True
        item.xyf_bound.start()
    return True


@pytest.hookimpl(tryfirst=True)
def pytest_timeout_cancel_timer(item):
    timer = getattr(item, 'xyf_bound', None)
    if timer:
        timer.cancel()
        del item.xyf_bound
    return True
