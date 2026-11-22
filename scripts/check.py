#!/usr/bin/env python3
"""The PR gate: the checks a pull request passes on this machine, before CI (mise.toml: how long it takes).

    uv run scripts/check.py [--js] [--force]     (mise run check)
    uv run scripts/check.py --confirm [REF]      (what the receipt for REF's tree records)

The steps, in order, stopping at the first failure:
  - the product build into the checkout's gitignored build/product, then xyf_build.py --verify of it, both through
    Turborepo's local cache (scripts/firmware_cache.py: an unchanged build and its verify are restored);
    a verified build is copied to <private>/xy-forever/builds/<image> (its name: the first 8 hex digits of the
    candidate's SHA-256, hello image=; installs take it from there), with check.json naming the tree and commit;
  - lint and type checks: the generated protocol files match protocol/ (protogen --check), `mise run lint`;
  - the TypeScript suites and type checks (`mise run web-test`) when clients/ts or web differ from the merge base
    with main (or with --js);
  - every tier but the sweeps in one pytest run (-m 'not sweep': tests/conftest.py, which enforces the CPU
    budgets). The tests read this checkout's own build/product (paths.verified_build()), which no other checkout
    writes; XYF_REQUIRE_BUILD fails a test that finds no usable build instead of skipping it.
First, when node_modules is missing or older than pnpm-lock.yaml (a fresh worktree), `pnpm install --frozen-lockfile`
(Turborepo and the type checks need it).
Wall time: a step over STEP_WALL_S or the whole over TOTAL_WALL_S is reported, never failed (a busy machine
stretches wall time; the suite's CPU budgets are what fail). Each step's hard bound is STEP_S.
Step output goes to a log under <private>/xy-forever/sessions/<date>-check/; a short summary is printed.

The receipt is a cache keyed on the tree: a run on a clean tree (`git status --porcelain` empty) where every
step passed writes <private>/xy-forever/checks/<tree>.json (`git rev-parse HEAD^{tree}`), and a later run on
the same tree prints it and stops (--force reruns). A failed run writes <tree>.failed.json. A dirty tree is
refused: commit first. A receipt never stands in for a device test, and CI is the merge check.
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import clean
import firmware_cache

from xy_forever import dev_tools, no_device, paths, procgroup

ROOT = Path(__file__).resolve().parents[1]

VERSION = 1  # receipt format; a reader refuses a newer one
CHECKS = paths.XYF / 'checks'  # receipts: <tree>.json (passed), <tree>.failed.json
STEP_S = 120  # seconds: a step past it ends with its process group (#230)
STEP_WALL_S = 15  # seconds: a step over it is reported (see the docstring)
TOTAL_WALL_S = 30
PYTEST = ('-m', 'pytest', '-n', 'auto', '--dist', 'worksteal')


class CheckError(Exception):
    """Why no receipt is written or confirmed (the message says what is missing)."""


def git(root, *args):
    run = subprocess.run(['git', *args], cwd=root, capture_output=True, text=True, timeout=60)
    if run.returncode:
        raise CheckError('git %s: %s' % (' '.join(args), run.stderr.strip() or 'failed'))
    return run.stdout.rstrip()


def tree_of(root, ref='HEAD'):
    """The receipt key: REF's tree hash."""
    return git(root, 'rev-parse', '--verify', '--quiet', ref + '^{tree}')


def require_clean(root, when):
    dirty = git(root, 'status', '--porcelain')
    if dirty:
        lines = dirty.splitlines()
        raise CheckError(
            'working tree not clean %s: no receipt (commit first)\n%s'
            % (when, '\n'.join(lines[:10] + (['... %d more' % (len(lines) - 10)] if len(lines) > 10 else [])))
        )


def merge_base(root):
    """HEAD's merge base with origin/main (else a local main, which may be stale), or None."""
    for main in ('origin/main', 'main'):
        try:
            return git(root, 'merge-base', 'HEAD', main)
        except CheckError:
            continue
    return None


def changed(root, *folders):
    """Whether any of folders differs between HEAD and its merge base with main (True without a main to compare)."""
    base = merge_base(root)
    if base is None:
        return True
    return (
        subprocess.run(['git', 'diff', '--quiet', base, 'HEAD', '--', *folders], cwd=root, timeout=60).returncode != 0
    )


def step(name, cmd, cwd, env=None, parse=None, timeout=STEP_S):
    """One check: a command, run in cwd with env added and ended (its whole process group) past timeout
    seconds; parse(exit status, output) -> (passed, facts)."""
    return dict(name=name, cmd=[str(c) for c in cmd], cwd=Path(cwd), env=env or {}, parse=parse, timeout=timeout)


def shown(s):
    """A step's command as recorded: env first, the interpreter as `uv run python`, paths relative to the
    repository."""
    words = [w for c in s['cmd'] for w in (('uv', 'run', 'python') if c == sys.executable else (c,))]
    words = [os.path.relpath(c, s['cwd']) if c.startswith(str(ROOT) + os.sep) else c for c in words]
    return shlex.join(['%s=%s' % kv for kv in s['env'].items()] + words)


def tests_parser(tier):
    """Passed when pytest exits 0; its counts (the summary line) go into the receipt."""

    def parse(status, text):
        counts = {
            k: int(n)
            for n, k in re.findall(
                r'(\d+) (passed|failed|skipped|errors?|deselected)\b',
                text.strip().splitlines()[-1] if text.strip() else '',
            )
        }
        return status == 0 and counts.get('passed', 0) > 0, dict(tests={tier: counts})

    return parse


def verify_parser(out, tree, commit):
    """--verify exited 0 and its verification.json in out records a pass, and the candidate's bytes match the
    build's manifest. Then a copy of the build goes to
    BUILDS/<image> (out itself stays: it is the cache's output), and check.json there maps it to the tree and
    commit."""

    def parse(status, text):
        candidate = out / 'candidate.ldr'
        if status or not paths.verified(out):
            return False, {}
        data = candidate.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        try:
            recorded = json.loads((out / 'manifest.json').read_text()).get('candidate_sha256')
        except OSError, ValueError:
            recorded = None
        if recorded != digest:
            return False, {}
        staged = paths.BUILDS / ('.check-' + tree[:12])
        shutil.rmtree(staged, ignore_errors=True)
        shutil.copytree(out, staged)
        write_json(staged / 'check.json', dict(image=digest[:8], tree=tree, commit=commit))
        named = paths.name_by_image(staged)
        return True, dict(candidate=dict(sha256=digest, image=named.name, bytes=len(data), build=str(named)))

    return parse


def standard_steps(args, root, tree):
    """The gate's steps for this run."""
    py = sys.executable
    js = js_steps(args, root)
    env = {paths.REQUIRE_BUILD: '1'}  # verify has just made this checkout's build: a test that cannot use it fails
    cached = root / 'scripts/firmware_cache.py'
    install = dev_tools.js_install(root)
    return [
        *([step('pnpm install', install, root)] if install else []),
        step('product build', [py, cached, 'build'], root),
        step(
            'verify',
            [py, cached, 'verify'],
            root,
            parse=verify_parser(firmware_cache.OUT, tree, git(root, 'rev-parse', 'HEAD')),
        ),
        step('protocol files', [py, root / 'scripts/protogen.py', '--check'], root),
        step('lint', [shutil.which('mise') or 'mise', 'run', '--quiet', 'lint'], root),
        *js,
        step('tests', [py, *PYTEST, '-m', 'not sweep'], root, env, tests_parser('every tier but the sweeps')),
    ]


JS = ('clients/ts', 'web')  # a change in either runs `mise run web-test` (the suites and type checks of both)


def js_steps(args, root):
    """`mise run web-test` when clients/ts or web changed (or with --js)."""
    if not any((root / rel).is_dir() for rel in JS) or not (args.js or changed(root, *JS)):
        return []
    return [step('web-test', [shutil.which('mise') or 'mise', 'run', '--quiet', 'web-test'], root)]


def over_wall(results):
    """The wall-time lines (reported only): a step over STEP_WALL_S, the whole over TOTAL_WALL_S."""
    lines = [
        '%s took %.1f s: over the %d s a step' % (r['name'], r['seconds'], STEP_WALL_S)
        for r in results
        if r['seconds'] > STEP_WALL_S
    ]
    total = sum(r['seconds'] for r in results)
    return lines + (['the check took %.1f s: over %d s' % (total, TOTAL_WALL_S)] if total > TOTAL_WALL_S else [])


def run_steps(steps, log):
    """Runs steps in order into the log, stopping at the first failure: [result], {facts}."""
    results, facts = [], {}
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, 'a') as f:
        for s in steps:
            f.write('\n=== %s: %s\n' % (s['name'], shown(s)))
            f.flush()
            start = time.monotonic()
            try:
                proc = procgroup.run(
                    s['cmd'],
                    cwd=s['cwd'],
                    env=dict(os.environ, **s['env']),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    errors='replace',
                    timeout=s['timeout'],
                )
                status, text = proc.returncode, proc.stdout
            except subprocess.TimeoutExpired as e:
                status, text = (
                    124,
                    (e.output or '') + '\nHUNG: %s ran past its %g s bound: ended\n' % (s['name'], s['timeout']),
                )
            except OSError as e:
                status, text = 127, str(e)
            f.write(text)
            f.write('\n=== exit %d\n' % status)
            passed, found = s['parse'](status, text) if s['parse'] else (status == 0, {})
            passed = passed and status == 0
            facts.update(found)
            results.append(
                dict(
                    name=s['name'],
                    command=shown(s),
                    result='pass' if passed else 'fail',
                    exit=status,
                    seconds=round(time.monotonic() - start, 1),
                )
            )
            print('  %-4s  %-24s %7.1f s' % (results[-1]['result'], s['name'], results[-1]['seconds']), flush=True)
            if not passed:
                break
    return results, facts


def now():
    return datetime.datetime.now(datetime.UTC).strftime('%Y-%m-%dT%H:%M:%SZ')


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.receipt-')
    with os.fdopen(fd, 'w') as f:
        json.dump(data, f, indent=2)
        f.write('\n')
    os.replace(tmp, path)


def check(root, steps_for, checks=CHECKS, log=None):
    """Runs steps_for(tree) on a clean tree and writes the receipt (every step passed) or the failure record.
    Returns the record; raises CheckError, writing nothing, when the tree is not clean at the start or end."""
    require_clean(root, 'at start')
    tree, commit = tree_of(root), git(root, 'rev-parse', 'HEAD')
    log = Path(
        log
        or paths.SESSIONS
        / (datetime.date.today().isoformat() + '-check')
        / ('%s-%s.log' % (tree[:12], time.strftime('%H%M%S')))
    )
    started = now()
    steps = steps_for(tree)
    results, facts = run_steps(steps, log)
    require_clean(root, 'at end')
    if tree_of(root) != tree:
        raise CheckError('HEAD changed during the run: no receipt')
    passed = len(results) == len(steps) and all(r['result'] == 'pass' for r in results)
    for line in over_wall(results) if passed else []:
        print('wall time (reported only): ' + line, file=sys.stderr)
    record = dict(
        version=VERSION,
        passed=passed,
        tree=tree,
        commit=commit,
        branch=git(root, 'rev-parse', '--abbrev-ref', 'HEAD'),
        started=started,
        finished=now(),
        steps=results,
        tests=facts.get('tests'),
        candidate=facts.get('candidate'),
        log=str(log),
    )
    receipt, failed = checks / (tree + '.json'), checks / (tree + '.failed.json')
    write_json(receipt if passed else failed, record)
    (failed if passed else receipt).unlink(missing_ok=True)
    return record


def load(path):
    """A receipt or failure record, or None when there is none; CheckError when unreadable or too new."""
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        raise CheckError('failed receipt: %s is unreadable (%s)' % (path, e))
    if not isinstance(data, dict) or not isinstance(data.get('version'), int):
        raise CheckError('failed receipt: %s has no version' % path)
    if data['version'] > VERSION:
        raise CheckError(
            '%s is receipt version %d, newer than this check.py reads (%d): update this checkout'
            % (path, data['version'], VERSION)
        )
    return data


def is_ancestor(root, commit, ref):
    return (
        subprocess.run(
            ['git', 'merge-base', '--is-ancestor', commit, ref], cwd=root, capture_output=True, timeout=60
        ).returncode
        == 0
    )


def older_receipt(root, ref, checks):
    """The newest passing receipt for REF's branch or an ancestor of REF, or None."""
    branch = git(root, 'rev-parse', '--abbrev-ref', ref)
    found = []
    for path in checks.glob('*.json'):
        if path.name.endswith('.failed.json'):
            continue
        try:
            data = load(path)
        except CheckError:
            continue
        if data and data.get('passed') is True:
            found.append(data)
    found.sort(key=lambda d: d.get('finished', ''), reverse=True)
    return next((d for d in found if d.get('branch') == branch), None) or next(
        (d for d in found if d.get('commit') and is_ancestor(root, d['commit'], ref)), None
    )


def confirm(root, ref='HEAD', checks=CHECKS):
    """The passing receipt for REF's tree; CheckError saying what is missing otherwise."""
    try:
        tree = tree_of(root, ref)
    except CheckError:
        raise CheckError('%s is not a commit here' % ref)
    receipt = load(checks / (tree + '.json'))
    if receipt is not None:
        if (
            receipt.get('passed') is not True
            or receipt.get('tree') != tree
            or not receipt.get('steps')
            or any(s.get('result') != 'pass' for s in receipt['steps'])
        ):
            raise CheckError(
                'failed receipt: %s does not record a pass for tree %s' % (checks / (tree + '.json'), tree)
            )
        return receipt
    failed = load(checks / (tree + '.failed.json'))
    if failed is not None:
        bad = [s.get('name') for s in failed.get('steps', []) if s.get('result') != 'pass']
        raise CheckError(
            'failed: the last check of %s (tree %s, %s) failed at %s; log %s'
            % (ref, tree[:12], failed.get('finished'), ', '.join(bad) or 'an unknown step', failed.get('log'))
        )
    older = older_receipt(root, ref, checks)
    if older:
        raise CheckError(
            'tree differs: %s has tree %s; the newest receipt for it covers tree %s (commit %s, %s). '
            'Rerun mise run check on %s'
            % (ref, tree[:12], older['tree'][:12], older.get('commit', '?')[:12], older.get('finished'), ref)
        )
    raise CheckError('no receipt for %s (tree %s): run mise run check on a clean checkout of it' % (ref, tree[:12]))


def describe(record, steps=True):
    """What a receipt records, as printed lines (steps: the step table too; a run has printed it already)."""
    lines = [
        '%s: tree %s, commit %s (%s), %s'
        % (
            'receipt' if record.get('passed') else 'FAILED check',
            record['tree'],
            record['commit'][:12],
            record.get('branch'),
            record.get('finished'),
        )
    ]
    for s in record['steps'] if steps else []:
        lines.append('  %-4s  %-24s %7.1f s  %s' % (s['result'], s['name'], s['seconds'], s['command']))
    t, c = record.get('tests'), record.get('candidate')
    for tier, counts in (t or {}).items():
        lines.append('tests, %s: %s' % (tier, ', '.join('%d %s' % (n, k) for k, n in counts.items())))
    if c:
        lines.append(
            'candidate: image %s, sha256 %s, %d bytes, %s'
            % (c.get('image', c['sha256'][:8]), c['sha256'], c['bytes'], c['build'])
        )
    lines.append('log: %s' % record.get('log'))
    return lines


def main(argv=None, root=ROOT, checks=CHECKS):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        '--confirm',
        nargs='?',
        const='HEAD',
        metavar='REF',
        help="exit 0 only when a passing receipt matches REF's tree (default HEAD)",
    )
    parser.add_argument('--js', action='store_true', help='the TypeScript suites and type checks even when unchanged')
    parser.add_argument('--force', action='store_true', help='run even when a passing receipt matches the tree')
    args = parser.parse_args(argv)
    no_device.switch_on()  # every step and its subprocesses: no real device
    try:
        if args.confirm:
            print('\n'.join(describe(confirm(root, args.confirm, checks))))
            return 0
        if not args.force:
            require_clean(root, 'at start')
            cached = load(checks / (tree_of(root) + '.json'))
            if cached and cached.get('passed') is True:
                print('\n'.join(describe(cached)))
                print('check: this tree already passed (the receipt above); --force reruns')
                return 0
        record = check(root, lambda tree: standard_steps(args, root, tree), checks)
        if record['passed']:
            clean.prune_builds()  # the one pruning of builds/ (scripts/clean.py), after the receipt
    except CheckError as e:
        print('check: %s' % e, file=sys.stderr)
        return 1
    print('\n'.join(describe(record, steps=False)))
    print(
        '%s: %s'
        % (
            'receipt' if record['passed'] else 'no receipt; failure recorded in',
            checks / (record['tree'] + ('.json' if record['passed'] else '.failed.json')),
        )
    )
    return 0 if record['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
