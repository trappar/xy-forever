"""The suite under pytest: tiers, enforced time budgets, hard bounds (xyf_bounds.py), --testmon's C++ and
script selection (xyf_select.py). Commands: `mise tasks` (test, test-changed, test-one, check, ...).

Tiers, each a marker set on a test: `gate`, `sim` (the simulator core, #233: each kept test says why in its
docstring) and `sweep` (power-cut and fault sweeps on the simulator, parked until they move to native tests:
`mise run test-sweeps`, in no routine run); every other test is `fast`. `mise run test` runs the fast tier
(-m fast), `mise run check` the fast and gate tiers, `mise run test-all` every tier but `sweep`.

Budgets: a test's time (setup, call and teardown, less the host compiles it waited for: a C++ edit never
fails a budget) and a run's wall time, per tier, below. Going over fails the run and names the test or the
tier. The limits sit at about twice the measured times, so a busy machine passes: a test over its tier's
limit is made faster, or moves to a slower tier. A run with --testmon (its coverage tracing slows every
test), the sweeps, or the sim tier with others (test-all) only reports them, as does XYF_BUDGETS=off (CI: other
hardware).
"""
import os
from pathlib import Path
import time

import pytest

from xy_forever import no_device
import xyf_bounds
import xyf_select

ROOT = Path(__file__).resolve().parents[1]

no_device.switch_on()                 # every test and every process it starts: no real device
os.chdir(ROOT)

# tier: (the run's wall time, one test's time), in seconds; None: not budgeted.
BUDGETS = {
    'fast': (3.0, 0.3),
    'gate': (15.0, 10.0),             # a check step: at most 15 s
    'sim': (5.0, 1.5),                # the simulator core (#233): 2.3 s measured (12 workers), the slowest test 0.7 s
                                      # (the boot partition writer: ~4,700 link messages); hard-killed at bounds.SIM_TEST_S
    'sweep': (None, None),            # parked sweeps: not budgeted
}
TIERS = tuple(BUDGETS)


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
    if not hasattr(config, 'workerinput'):
        config.pluginmanager.register(Budgets(), 'xyf-budgets')


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(items):
    for item in items:
        tier = tier_of(item)
        item.add_marker(tier)                       # before -m filters (it selects by tier)
        if tier in ('sim', 'sweep'):
            item.add_marker(pytest.mark.uses('sim/'))
        item.user_properties.append(('xyf_tier', tier))


class Budgets:
    """Controller side: sums each test's phases (less compile time) and checks the tiers at the end."""
    def __init__(self):
        self.start, self.tests, self.over, self.enforced = time.monotonic(), {}, [], True

    def pytest_runtest_logreport(self, report):
        props = dict(report.user_properties)
        entry = self.tests.setdefault(report.nodeid, [props.get('xyf_tier', 'fast'), 0.0])
        entry[1] += report.duration - props.get('xyf_compile_s', 0.0)

    def pytest_sessionfinish(self, session):
        wall = time.monotonic() - self.start
        tiers = {tier for tier, _ in self.tests.values()}
        for nodeid, (tier, seconds) in sorted(self.tests.items()):
            limit = BUDGETS[tier][1]
            if limit is not None and seconds > limit:
                self.over.append('%s took %.2f s: over the %s tier\'s %g s a test' % (nodeid, seconds, tier, limit))
        limit = sum(BUDGETS[t][0] or 0 for t in tiers)
        if tiers and 'sweep' not in tiers and wall > limit:
            self.over.append('the %s tier%s took %.1f s: over %g s' % (
                ' and '.join(t for t in TIERS if t in tiers), 's' if len(tiers) > 1 else '', wall, limit))
        # Measured alone: a run that mixes the sim tier with others (test-all: 12 jobs) loads the fast tests too.
        self.enforced = (os.environ.get('XYF_BUDGETS') != 'off' and 'sweep' not in tiers
                         and (tiers == {'sim'} or 'sim' not in tiers)
                         and not session.config.getoption('testmon', False))
        if self.over and self.enforced and session.exitstatus == 0:
            session.exitstatus = pytest.ExitCode.TESTS_FAILED

    def pytest_terminal_summary(self, terminalreporter):
        for line in self.over:
            terminalreporter.write_line(('BUDGET: ' if self.enforced else 'budget (not enforced): ') + line,
                                        red=self.enforced)
