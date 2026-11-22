"""The suite under pytest: tiers, enforced CPU budgets, hard bounds (xyf_bounds.py), --testmon's C++ and
script selection (xyf_select.py). The commands and the policy: docs/operating/checks.md.

Tiers, each a marker on a test (the closest marker wins): `gate`, `sim` (the simulator core, #233: each kept
test says why in its docstring) and `sweep` (simulator sweeps too long to share a run: `mise run test-sweeps`);
every other test is `fast`. `mise run test` runs the fast tier (named tests: any tier), `mise run check` and
`mise run test-all` every tier but `sweep`.

Budgets (BUDGETS), on CPU time: one test's, and the sum of a tier's tests'. A test's CPU time sums its setup,
call and teardown: this process (every thread) and the child processes it waited for (a simulator it stopped,
a binary it ran); the host C++ is built before the run (`uv sync`), never in a test. A busy machine stretches wall
time; CPU time only a little (measured: 1.15-1.25x next to two other full runs). Every limit scales by probe_s()
against PROBE_S, at most MAX_SCALE: slower hardware raises them (never below the table); XYF_BUDGETS_SCALE=N fixes
the scale instead. --testmon's coverage tracing scales them by TRACING. Going over
fails the run, everywhere (CI too), and names the test or tier: make the test faster, or move it to a slower
tier. Wall time is only reported (the run's, against its tiers' wall figures): the hard bounds (xyf_bounds.py)
end a hang. XYF_BUDGETS=off only reports.
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

from xy_forever import bounds, no_device, paths

ROOT = Path(__file__).resolve().parents[1]

no_device.switch_on()  # every test and every process it starts: no real device
os.chdir(ROOT)

# tier: (the CPU seconds of all its tests, one test's CPU seconds, the run's wall seconds: reported only). The
# limits are about 1.3x what -n auto measured on the reference machine (fast 3.8 s, slowest 0.2; gate 77 s, slowest
# 5.5; sim 10 s, slowest 1.35; sweep 22 s, slowest 5.2: a class's setup runs once in each worker that takes one
# of its tests). A fast test's 0.3 s is the tier's definition.
BUDGETS = {
    'fast': (5.0, 0.3, 3.0),
    'gate': (100.0, 7.5, 15.0),
    'sim': (13.0, 1.8, 5.0),  # the simulator core (#233); hard-killed at bounds.SIM_TEST_S
    'sweep': (29.0, 7.0, 15.0),  # each a 3-9 s serial chain of simulator restarts: alone, not in a shared run
}
TIERS = tuple(BUDGETS)
PROBE_S = 0.015  # probe_s() on the reference machine (Apple M5 Max): more scales the limits
MAX_SCALE = 3.0  # CI's Linux runners probe at about 2x
FAILURES = ROOT / 'build' / 'test-failures.log'  # every run's failures, appended (FailureLog)
TRACING = 2.5  # --testmon: coverage tracing took 1.6x a test's CPU time (median), 2.2x at worst


def verified_build(test):
    """The verified product build of this checkout's sources (paths.verified_build()). Without one, test is
    skipped naming why, or failed when XYF_REQUIRE_BUILD is set (mise run check sets it: its verify step has
    just made one, so a build it cannot use is a bug)."""
    build, why = paths.verified_build()
    if build is None:
        (test.fail if os.environ.get(paths.REQUIRE_BUILD) else test.skipTest)(why)
    return build


def tier_of(item):
    return next((m.name for m in item.iter_markers() if m.name in TIERS), 'fast')


def pytest_configure(config):  # a conftest's runs before pytest-timeout's, which reads the setting
    if config.getoption('timeout') is None:  # --timeout=N wins
        config.option.timeout = bounds.TEST_S  # past it, HUNG (xyf_bounds.py)
    config.addinivalue_line('markers', 'fast: the fast tier (every test not gate or sim)')
    config.addinivalue_line('markers', 'gate: the gate tier: run by mise run check, not mise run test')
    config.addinivalue_line('markers', 'sim: the simulator core (#233): mise run test-all and CI')
    config.addinivalue_line('markers', 'sweep: a long simulator sweep, in no shared run: mise run test-sweeps')
    config.pluginmanager.register(xyf_bounds, 'xyf-bounds')
    xyf_select.register(config)
    config.pluginmanager.register(CpuClock(), 'xyf-cpu')
    if not hasattr(config, 'workerinput'):
        config.pluginmanager.register(Budgets(config), 'xyf-budgets')
        if config.rootpath == ROOT:  # not a planted run (test_budgets, test_hang_bounds)
            config.pluginmanager.register(FailureLog(), 'xyf-failures')


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(items):
    for item in items:
        tier = tier_of(item)
        item.add_marker(tier)  # before -m filters (it selects by tier)
        if tier in ('sim', 'sweep'):
            item.add_marker(pytest.mark.uses('sim/'))
        item.user_properties.append(('xyf_tier', tier))


@pytest.hookimpl(tryfirst=True)
def pytest_sessionstart(session):
    """Quiet runs (-q, the default): no xdist "bringing up nodes..." lines (twice when not on a terminal)."""
    dsession = session.config.pluginmanager.getplugin('dsession')
    if dsession is not None and session.config.get_verbosity() < 0:
        dsession.trdist.ensure_show_status = lambda: None


@pytest.hookimpl(tryfirst=True)
def pytest_report_teststatus(report, config):
    """A passing subtest counts in the summary, without a progress mark of its own unless verbose (-v)."""
    if hasattr(report, 'context') and report.passed and config.get_verbosity() < 1:
        return 'subtests passed', '', ''
    return None


def cpu_s():
    """CPU seconds of this process (every thread) and of the child processes it has waited for."""
    children = resource.getrusage(resource.RUSAGE_CHILDREN) if resource else None
    return time.process_time() + (children.ru_utime + children.ru_stime if children else 0.0)


def probe_s():
    """CPU seconds of a fixed workload (Python and hashing, as the fast tests mix them), the least of three: PROBE_S
    on the reference machine, more on slower hardware. CPU time, as the budgets: a wall-time probe read a busy
    machine's queueing as up to 9x, while the tests' CPU time grew 1.25x at most."""
    times = []
    for _ in range(3):
        start, total = time.process_time(), 0
        for i in range(50_000):
            digest = hashlib.sha256(struct.pack('<II', i, total & 0xFFFFFFFF)).digest()
            total += sum(digest[:8]) + len({i: digest})
        times.append(time.process_time() - start)
    return min(times)


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
        if self.mode not in ('on', 'off'):
            raise pytest.UsageError('XYF_BUDGETS=%s: on (the default) or off' % self.mode)
        self.fixed = float(os.environ['XYF_BUDGETS_SCALE']) if os.environ.get('XYF_BUDGETS_SCALE') else None
        self.tracing = (
            TRACING if config.getoption('testmon', False) or config.getoption('testmon_forceselect', False) else 1.0
        )
        self.probe = probe_s()  # before the workers start, and again after they end
        self.start, self.tests, self.lines, self.failed = time.monotonic(), {}, [], False

    def pytest_runtest_logreport(self, report):
        if hasattr(report, 'context'):  # a subtest's report: its time is in its test's call phase
            return
        props = dict(report.user_properties)
        entry = self.tests.setdefault(report.nodeid, [props.get('xyf_tier', 'fast'), 0.0])
        entry[1] += props.get('xyf_cpu_s', report.duration)

    def pytest_sessionfinish(self, session):
        wall = time.monotonic() - self.start
        self.probe = max(self.probe, probe_s())
        scale = (self.fixed or min(MAX_SCALE, max(1.0, self.probe / PROBE_S))) * self.tracing
        tiers = {}
        for nodeid, (tier, cpu) in sorted(self.tests.items()):
            tiers.setdefault(tier, []).append((cpu, nodeid))
            limit = BUDGETS[tier][1] * scale
            if cpu > limit:
                self.over("%s took %.2f s of CPU: over the %s tier's %.2f s a test" % (nodeid, cpu, tier, limit))
        shown = []
        for tier in (t for t in TIERS if t in tiers):
            total, (slowest, _) = sum(c for c, _ in tiers[tier]), max(tiers[tier])
            limit = BUDGETS[tier][0] * scale
            if total > limit:
                self.over('the %s tier took %.1f s of CPU: over its %.1f s' % (tier, total, limit))
            shown.append(
                '%s %.1f of %.0f s (slowest test %.2f of %.2f s)'
                % (tier, total, limit, slowest, BUDGETS[tier][1] * scale)
            )
        if shown:
            self.lines.append('budgets, CPU (x%.2f): ' % scale + '; '.join(shown))
            figure = sum(BUDGETS[t][2] for t in tiers) * scale
            if wall > figure:  # reported, never failed: a busy machine stretches wall time
                self.lines.append("wall time (reported only): %.1f s, over the tiers' %.1f s" % (wall, figure))
        if self.failed and session.exitstatus == 0:
            session.exitstatus = pytest.ExitCode.TESTS_FAILED

    def over(self, line):
        """Over a CPU budget: fails the run unless XYF_BUDGETS=off."""
        self.failed |= self.mode == 'on'
        self.lines.append(('BUDGET: ' if self.mode == 'on' else 'budget (not enforced): ') + line)

    def pytest_terminal_summary(self, terminalreporter):
        for line in self.lines:
            terminalreporter.write_line(line, red=line.startswith('BUDGET'))


class FailureLog:
    """Controller side: every failure (a test, a subtest, a crashed worker, a budget) is appended, with its
    output, to FAILURES, so an intermittent one names its test after the terminal is gone."""

    def __init__(self):
        self.lines = []

    def pytest_runtest_logreport(self, report):
        if report.failed:
            context = getattr(report, 'context', None)  # a subtest's: its message or parameters
            sub = context and (context.msg or context.kwargs)
            name = report.nodeid + (' [subtest %s]' % (sub,) if context else '')
            self.lines.append('FAILED %s (%s)\n%s\n' % (name, report.when, report.longreprtext))

    def pytest_terminal_summary(self, terminalreporter, config):
        budgets = config.pluginmanager.get_plugin('xyf-budgets')
        self.lines += [line + '\n' for line in budgets.lines if line.startswith('BUDGET')]
        if self.lines:
            FAILURES.parent.mkdir(parents=True, exist_ok=True)
            with open(FAILURES, 'a') as f:
                f.write(
                    '== %s: pytest %s\n' % (time.strftime('%Y-%m-%d %H:%M:%S'), ' '.join(config.invocation_params.args))
                )
                f.writelines(self.lines)
            terminalreporter.write_line('failures logged: %s' % FAILURES.relative_to(ROOT), red=True)
