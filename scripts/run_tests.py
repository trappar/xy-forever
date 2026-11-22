#!/usr/bin/env python3
"""Runs the unit tests (tests/test_*.py), test modules in parallel, and shows where the time goes.

    python3 scripts/run_tests.py                every module, one process each, --jobs at a time
                                                (the modules in SPLIT: one process per class or test)
    python3 scripts/run_tests.py --jobs 1       in this process, in order (as unittest discover does)
    python3 scripts/run_tests.py --times        also the slowest modules and tests
    python3 scripts/run_tests.py test_simulator test_aes.NativeAesTests   only these
    python3 scripts/run_tests.py --summary FILE also the counts as JSON in FILE (scripts/check.py)

Each test is charged the wall time since the previous test of its process finished, so class
and module fixtures (bridge compiles, simulator starts) land on the first test that needed
them. A module's output is shown only when it failed. Exit 1 if anything failed or errored.

Hard bounds (#230, xy_forever/bounds.py): a watchdog in each process ends it when one test, or the
fixtures between two tests, runs past bounds.TEST_S (SLOW_S for a test marked @bounds.slow), with
every thread's traceback; a process past bounds.PROCESS_S gets SIGTERM (the same tracebacks), then
SIGKILL, with its whole process group. Either way the run names it: HUNG: <test id>.

The longest work starts first: each run records its per-test times (TIMES, outside git; one record per
mode, as XYF_EXHAUSTIVE=1 runs some tests much longer, and a full run replaces it, dropping tests that
are gone) and the next run orders its processes by them; without a record, FIRST sets the order.
"""
import argparse
import collections
import concurrent.futures
import faulthandler
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / 'tests'
sys.path.insert(0, str(ROOT))
from xy_forever import bounds, no_device, paths, procgroup  # noqa: E402

TIMES = paths.SESSIONS / ('run-tests-times%s.json' % ('-exhaustive' if os.environ.get('XYF_EXHAUSTIVE') else ''))
JOBS = max(1, min(16, (os.cpu_count() or 2) * 2 // 3))   # simulator tests start two threads each
FIRST = ('test_simulator', 'test_conformance', 'test_ts_client', 'test_p224',
         'test_tfw_os', 'test_protocol_spec')    # the longest modules start first (any order is correct)
# Modules too slow for one process, split by test (a cheap setUpClass: a cached simulator build and a
# seeded library) or by class (an expensive setUpClass: bridge and sanitizer builds, run once a class).
SPLIT = {'test_simulator': 'test', 'test_sim_mod_job': 'test', 'test_sim_content_switch': 'test',
         'test_sim_config': 'test', 'test_mod_link': 'class', 'test_modpatch': 'class',
         'test_sim_mods_page': 'class', 'test_modsend': 'class', 'test_sim_memory': 'class',
         'test_ts_client': 'class',      # NodeSuiteTest (pnpm test, up to ~120 s) in its own process
         'test_p224': 'test',            # exhaustive: every bit of each official header, one test a package
         'test_mod_job': 'test'}         # exhaustive: the published job's faults at every step, in four tests


class TimedResult(unittest.TextTestResult):
    current = None       # a file kept naming the test (or fixtures) running, for the runner's HUNG line
    limit = None         # every bound in seconds (--test-timeout), else TEST_S, or SLOW_S for @bounds.slow

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.mark = time.monotonic()
        self.times = []
        self.watch('the fixtures before the first test', bounds.TEST_S)

    def watch(self, label, seconds):
        """The watchdog: past seconds, every thread's traceback and exit 1 (faulthandler: even in C code)."""
        if self.current:
            Path(self.current).write_text(label)
        faulthandler.dump_traceback_later(self.limit or seconds, exit=True)

    def startTest(self, test):
        super().startTest(test)
        bounds.slow_now = bounds.is_slow(test)
        self.watch(test.id(), bounds.SLOW_S if bounds.slow_now else bounds.TEST_S)

    def stopTest(self, test):
        super().stopTest(test)
        now = time.monotonic()
        self.times.append((test.id(), now - self.mark))
        self.mark = now
        bounds.slow_now = False
        self.watch('the fixtures after ' + test.id(), bounds.TEST_S)


def run_here(names, stream):
    """Runs tests in this process: a dict of counts, failure texts and per-test times."""
    sys.path[:0] = [str(TESTS), str(ROOT)]
    os.chdir(ROOT)
    loader = unittest.TestLoader()
    suite = (loader.loadTestsFromNames(names) if names
             else loader.discover(str(TESTS), top_level_dir=str(TESTS)))
    try:
        result = unittest.TextTestRunner(stream=stream, resultclass=TimedResult, verbosity=0).run(suite)
    finally:
        faulthandler.cancel_dump_traceback_later()
    return dict(run=result.testsRun, skipped=len(result.skipped), times=result.times,
                problems=[(kind, test.id(), text) for kind, items in (('FAIL', result.failures),
                          ('ERROR', result.errors), ('UNEXPECTED SUCCESS', [(t, '') for t in
                                                                          result.unexpectedSuccesses]))
                          for test, text in items])


def run_unit(names, test_s=None, process_s=bounds.PROCESS_S):
    """Some tests (a module, a class, a test) in their own process group, ended past process_s (output
    kept for a failure). A process its watchdog or this bound ended: HUNG and what was running."""
    with tempfile.TemporaryDirectory(prefix='xyf-tests-') as tmp:
        report = Path(tmp) / 'report.json'
        argv = [sys.executable, __file__, '--worker', str(report)]
        argv += ['--test-timeout', str(test_s)] if test_s else []
        try:
            proc = procgroup.run(argv + names, timeout=process_s, cwd=ROOT, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True, errors='replace')
            status, output = proc.returncode, proc.stdout or ''
        except subprocess.TimeoutExpired as e:
            status, output = None, e.output or ''
        if report.is_file():
            result = json.loads(report.read_text())
        else:
            running = report.with_name('current')
            running = running.read_text() if running.is_file() else ' '.join(names)
            if status is None:
                problem = ('HUNG', running, 'its process ran past %g s: ended with its group' % process_s)
            elif 'Timeout (' in output:                               # faulthandler's watchdog
                problem = ('HUNG', running, 'ran past its bound: its process ended with its group')
            else:
                problem = ('ERROR', running, 'worker exited %d' % status)
            result = dict(run=0, skipped=0, times=[], problems=[problem])
        result['output'] = output
        return result


def split(module):
    """A SPLIT module's processes: [[test ids]] by class or by test ([[module]] if it does not load)."""
    sys.path[:0] = [str(TESTS), str(ROOT)]
    ids = []
    stack = [unittest.TestLoader().loadTestsFromName(module)]
    while stack:
        item = stack.pop(0)
        if isinstance(item, unittest.TestSuite):
            stack[:0] = list(item)
        else:
            ids.append(item.id())
    if not ids or any(not i.startswith(module + '.') for i in ids):
        return [[module]]                              # an import error: the worker reports it
    if SPLIT[module] == 'test':
        return [[i] for i in ids]
    groups = {}
    for i in ids:
        groups.setdefault(i.rsplit('.', 1)[0], []).append(i)
    return [[name] for name in groups]


def units(names, times):
    """The processes to run, longest first (recorded times, else FIRST, else name order)."""
    if not names:
        found = sorted(p.stem for p in TESTS.glob('test*.py'))
        names = [m for m in FIRST if m in found] + [m for m in found if m not in FIRST]
    work = [u for n in names for u in (split(n) if n in SPLIT else [[n]])]

    def expected(unit):
        return sum(s for test, s in times.items() for n in unit if test == n or test.startswith(n + '.'))
    rank = {u[0]: i for i, u in enumerate(work)}
    return sorted(work, key=lambda u: (-expected(u), rank[u[0]]))


def recorded():
    try:
        return json.loads(TIMES.read_text())
    except (OSError, ValueError):
        return {}


def record(times, results):
    """Adds this run's per-test times to TIMES (shared by worktrees; a lost update only costs order); a full
    run passes times={} and replaces it."""
    times = dict(times, **{test: round(s, 3) for r in results for test, s in r['times']})
    try:
        TIMES.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=TIMES.parent, prefix='.run-tests-')
        with os.fdopen(fd, 'w') as f:
            json.dump(times, f, indent=0, sort_keys=True)
        os.replace(tmp, TIMES)
    except OSError:
        pass


def table(results, top):
    times = [t for r in results for t in r['times']]
    per_module = collections.Counter()
    for name, seconds in times:
        per_module[name.split('.')[0]] += seconds
    print('\nslowest modules:')
    for name, seconds in per_module.most_common(top):
        print('%7.2f s  %s' % (seconds, name))
    print('\nslowest tests (including fixtures they triggered):')
    for name, seconds in sorted(times, key=lambda item: -item[1])[:top]:
        print('%7.2f s  %s' % (seconds, name))


def main():
    no_device.switch_on()                        # every test process and what it runs: no real device
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('names', nargs='*', help='modules or tests (default: every tests/test*.py)')
    parser.add_argument('--jobs', type=int, default=JOBS,
                        help='processes at a time (default %(default)s; 1: in this process, in order)')
    parser.add_argument('--times', action='store_true', help='print the slowest modules and tests')
    parser.add_argument('--top', type=int, default=15, help='rows per --times table (default %(default)s)')
    parser.add_argument('--summary', metavar='FILE', help='write the counts as JSON: run, skipped, failed, seconds, jobs')
    parser.add_argument('--test-timeout', type=float, metavar='S',
                        help='every test\'s bound instead of bounds.TEST_S and SLOW_S (tests of the bounds)')
    parser.add_argument('--process-timeout', type=float, default=bounds.PROCESS_S, metavar='S',
                        help='each process\'s bound (default %(default)g s)')
    parser.add_argument('--worker', metavar='REPORT', help=argparse.SUPPRESS)
    args = parser.parse_args()
    TimedResult.limit = args.test_timeout
    if args.worker:
        TimedResult.current = Path(args.worker).with_name('current')
        faulthandler.register(signal.SIGTERM, all_threads=True)   # the runner's bound: where it hung
        procgroup.end_group_with_parent()                          # the runner gone: this group too
        stream = io.StringIO()
        result = run_here(args.names, stream)
        print(stream.getvalue())
        Path(args.worker).write_text(json.dumps(result))
        return 0
    start = time.monotonic()
    if args.jobs == 1:
        results = [run_here(args.names, sys.stderr)]
    else:
        times = recorded()
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs)
        try:
            results = list(pool.map(lambda u: run_unit(u, args.test_timeout, args.process_timeout),
                                    units(args.names, times)))
        except KeyboardInterrupt:
            os._exit(130)              # at once: each worker's group ends with this process
        pool.shutdown()
        record(times if args.names else {}, results)
        for r in results:
            if r['problems']:
                print(r['output'], file=sys.stderr)
    total = time.monotonic() - start
    problems = [p for r in results for p in r['problems']]
    if args.times:
        table(results, args.top)
    for kind, name, _ in problems:
        print('%s: %s' % (kind, name))
    run, skipped = sum(r['run'] for r in results), sum(r['skipped'] for r in results)
    print('\n%d tests, %d skipped, %d failed or errored, %.1f s (%s)' % (
        run, skipped, len(problems), total, 'in order' if args.jobs == 1 else '%d jobs' % args.jobs))
    if args.summary:
        Path(args.summary).write_text(json.dumps(dict(run=run, skipped=skipped, failed=len(problems),
                                                      seconds=round(total, 1), jobs=args.jobs)))
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())
