"""The suite under pytest: tiers, enforced time budgets, hard bounds (xyf_bounds.py), --testmon's C++ and
script selection (xyf_select.py). Commands: `mise tasks` (test, test-changed, test-one, check, ...).

Tiers, each a marker set on a test: `gate`, `sim` (the simulator core, #233: each kept test says why in its
docstring) and `sweep` (power-cut and fault sweeps on the simulator, parked until they move to native tests:
`mise run test-sweeps`, in no routine run); every other test is `fast`. `mise run test` runs the fast tier
(-m fast; named files or node ids: any tier), `mise run check` the fast, sim and gate tiers, `mise run test-all`
every tier but `sweep`.

Budgets, per tier below: one test's time and the run's wall time. A test's time sums its setup, call and
teardown, less the host compiles it waited for (a C++ edit never fails a budget). It is CPU time (this process
and the child processes it waited for), or wall time when that is less, since a busy machine stretches wall time
but barely CPU time; a sim tier test's is wall time, as its work runs in simulator processes that outlive it.
Every limit scales by probe_s() against PROBE_S: slower hardware, or a busy machine at the run's start or end,
raises them (never below the table). --testmon's coverage tracing scales them by TRACING. Going over fails the
run and names the test or tier: make the test faster, or move it to a slower tier. Runs that include the sweeps
are not budgeted. XYF_BUDGETS=ci (CI: other hardware, another core count) enforces the CPU budgets and only
reports the wall-time ones (the run's total and a sim test's); XYF_BUDGETS=off only reports.
"""

import hashlib
import os
import struct
import time
from pathlib import Path

try:
    import resource
except ImportError:  # Windows: the CPU time of child processes is not counted
    resource = None

import pytest
import xyf_bounds
import xyf_select

from xy_forever import no_device

ROOT = Path(__file__).resolve().parents[1]

no_device.switch_on()  # every test and every process it starts: no real device
os.chdir(ROOT)

# tier: (the run's wall time, one test's time), in seconds; None: not budgeted.
BUDGETS = {
    'fast': (3.0, 0.3),
    'gate': (15.0, 10.0),  # a check step: at most 15 s
    'sim': (5.0, 1.5),  # the simulator core (#233): 2.3 s measured (12 workers), the slowest test 0.7 s
    # (the boot partition writer: ~4,700 link messages); hard-killed at bounds.SIM_TEST_S
    'sweep': (None, None),  # parked sweeps: not budgeted
}
TIERS = tuple(BUDGETS)
PROBE_S = 0.035  # probe_s() on the reference machine (Apple M5 Max), idle: more scales the limits
TRACING = 2.5  # --testmon: coverage tracing took 1.6x a test's CPU time (median), 2.2x at worst


def tier_of(item):
    for name in ('sweep', 'sim', 'gate'):
        if item.get_closest_marker(name):
            return name
    return 'fast'


def pytest_configure(config):
    config.addinivalue_line('markers', 'fast: the fast tier (every test not gate or sim)')
    config.addinivalue_line('markers', 'gate: the gate tier: run by mise run check, not mise run test')
    config.addinivalue_line('markers', 'sim: the simulator core (#233): mise run test-all and CI')
    config.addinivalue_line('markers', 'sweep: a simulator sweep waiting to move to native tests: mise run test-sweeps')
    config.pluginmanager.register(xyf_bounds, 'xyf-bounds')
    xyf_select.register(config)
    config.pluginmanager.register(CpuClock(), 'xyf-cpu')
    if not hasattr(config, 'workerinput'):
        config.pluginmanager.register(Budgets(config), 'xyf-budgets')


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(items):
    for item in items:
        tier = tier_of(item)
        item.add_marker(tier)  # before -m filters (it selects by tier)
        if tier in ('sim', 'sweep'):
            item.add_marker(pytest.mark.uses('sim/'))
        item.user_properties.append(('xyf_tier', tier))


def cpu_s():
    """CPU seconds of this process (every thread) and of the child processes it has waited for."""
    children = resource.getrusage(resource.RUSAGE_CHILDREN) if resource else None
    return time.process_time() + (children.ru_utime + children.ru_stime if children else 0.0)


def probe_s():
    """Wall seconds of a fixed workload (Python and hashing, as the fast tests mix them): PROBE_S on the
    reference machine when idle; more on slower hardware or a busy machine (queued, or on efficiency cores)."""
    start, total = time.perf_counter(), 0
    for i in range(100_000):
        digest = hashlib.sha256(struct.pack('<II', i, total & 0xFFFFFFFF)).digest()
        total += sum(digest[:8]) + len({i: digest})
    return time.perf_counter() - start


class CpuClock:
    """Worker side (or the only process): each phase's CPU time, in its report's user_properties."""

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_setup(self, item):
        return (yield from self.timed())

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_call(self, item):
        return (yield from self.timed())

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_teardown(self, item):
        return (yield from self.timed())

    def timed(self):
        start = cpu_s()
        try:
            return (yield)
        finally:
            self.phase_s = cpu_s() - start

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_makereport(self, item, call):
        report = yield
        report.user_properties.append(('xyf_cpu_s', self.phase_s))
        return report


class Budgets:
    """Controller side: sums each test's phases (less compiles) and checks the tests and tiers at the end."""

    def __init__(self, config):
        self.mode = os.environ.get('XYF_BUDGETS', 'on')
        if self.mode not in ('on', 'ci', 'off'):
            raise pytest.UsageError('XYF_BUDGETS=%s: on (the default), ci or off' % self.mode)
        self.tracing = TRACING if config.getoption('testmon', False) else 1.0
        self.probe = probe_s()  # before the workers start, and again after they end
        self.start, self.tests, self.over, self.failed = time.monotonic(), {}, [], False

    def pytest_runtest_logreport(self, report):
        if hasattr(report, 'context'):  # a subtest's report: its time is in its test's call phase
            return
        props = dict(report.user_properties)
        entry = self.tests.setdefault(report.nodeid, [props.get('xyf_tier', 'fast'), 0.0, 0.0])
        entry[1] += report.duration - props.get('xyf_compile_s', 0.0)
        entry[2] += props.get('xyf_cpu_s', report.duration) - props.get('xyf_compile_cpu_s', 0.0)

    def pytest_sessionfinish(self, session):
        wall = time.monotonic() - self.start
        self.probe = max(self.probe, probe_s())
        self.scale = max(1.0, self.probe / PROBE_S) * self.tracing
        tiers = {tier for tier, _, _ in self.tests.values()}
        for nodeid, (tier, wall_s, cpu) in sorted(self.tests.items()):
            limit, on_cpu = BUDGETS[tier][1], tier != 'sim'
            seconds = min(wall_s, cpu) if on_cpu else wall_s
            if limit is not None and seconds > limit * self.scale:
                self.note(
                    on_cpu,
                    "%s took %.2f s%s: over the %s tier's %g s a test"
                    % (nodeid, seconds, ' of CPU' if on_cpu and cpu < wall_s else '', tier, limit * self.scale),
                )
        limit = sum(BUDGETS[t][0] or 0 for t in tiers) * self.scale
        if tiers and 'sweep' not in tiers and wall > limit:
            self.note(
                False,
                'the %s tier%s took %.1f s: over %g s'
                % (' and '.join(t for t in TIERS if t in tiers), 's' if len(tiers) > 1 else '', wall, limit),
            )
        if self.failed and session.exitstatus == 0:
            session.exitstatus = pytest.ExitCode.TESTS_FAILED

    def note(self, on_cpu, line):
        """CPU budgets fail the run unless XYF_BUDGETS=off; wall-time ones also report only under ci."""
        enforced = self.mode == 'on' or (self.mode == 'ci' and on_cpu)
        self.failed |= enforced
        self.over.append(('BUDGET: ' if enforced else 'budget (not enforced): ') + line)

    def pytest_terminal_summary(self, terminalreporter):
        for line in self.over:
            terminalreporter.write_line(line, red=line.startswith('BUDGET'))
