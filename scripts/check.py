#!/usr/bin/env python3
"""Run the standard checks once and leave a receipt tied to the exact source tree.

    python3 scripts/check.py [--jobs N] [--neutral [REF]] [--npm]
    python3 scripts/check.py --confirm [REF]      (default REF: HEAD; fast, builds nothing)

The standard set, in order, stopping at the first failure:
  - the product build into a fresh private build folder (<private>/xy-forever/builds/check-<tree>),
    then xyf_build.py --verify of it; a verified build moves to builds/<image> (its name: the first 8 hex
    digits of the candidate's SHA-256, hello image=), with check.json naming the tree and commit;
  - the exhaustive unit suite: XYF_EXHAUSTIVE=1 scripts/run_tests.py (every test, never a subset), after
    the build, with a private copy of the verified artifact for the tests so a shared image name can be
    replaced during the run without affecting them;
  - scripts/neutral.py REF with --neutral (default REF: the merge base with origin/main, else main);
  - npm test and npm run typecheck in web/ when web/ or clients/ts (which it uses) differs from that merge
    base, or with --npm. npm test in clients/ts runs once, inside the suite (NodeSuiteTest); when clients/ts
    differs (or with --npm) its node_modules is required so that test cannot skip. Refused up front, naming
    npm ci, when a needed folder's node_modules is missing.
Step output goes to a log under <private>/xy-forever/sessions/<date>-check/; a short summary is printed.

The working tree must be clean (`git status --porcelain` empty: untracked files count, gitignored ones
do not) when the run starts and when it ends. Only a run on a clean tree where every step passed writes
the receipt <private>/xy-forever/checks/<tree>.json, keyed to `git rev-parse HEAD^{tree}`: the same
files give the same key whatever the commit. A run where a step failed writes no receipt: it removes
that tree's receipt and records the failure in <tree>.failed.json. --confirm exits 0 only when a
passing receipt matches REF's tree and prints what it records; otherwise it says what is missing (no
receipt, a failed check, or a receipt for an older tree of the branch). A receipt never stands in for
a device test. One full run at a time on this machine: a run holds <private>/xy-forever/checks/check.lock
(xy_forever/device_lock.py) until it exits, and a second one says so and waits (two at once starve the simulator
timing tests). A single run never waits; a test run, or one with no private folder, takes no lock.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from xy_forever import device_lock, no_device, paths, procgroup  # noqa: E402

VERSION = 1                                  # receipt format; a reader refuses a newer one
CHECKS = paths.XYF / 'checks'                # receipts: <tree>.json (passed), <tree>.failed.json
KEEP_BUILDS = 5                              # builds/<image> folders a check made (check.json) kept, ~7 MB each
RECENT = 15 * 60                             # seconds: a build folder touched since then is never pruned
STEP_S = 300                                 # seconds: a step past it ends with its process group (#230)
SUITE_S = 480                                # the exhaustive suite's bound (~2-3 min today)


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
        raise CheckError('working tree not clean %s: no receipt (commit first)\n%s' % (
            when, '\n'.join(lines[:10] + (['... %d more' % (len(lines) - 10)] if len(lines) > 10 else []))))


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
    return subprocess.run(['git', 'diff', '--quiet', base, 'HEAD', '--', *folders], cwd=root, timeout=60).returncode != 0


def step(name, cmd, cwd, env=None, parse=None, timeout=STEP_S):
    """One check: a command, run in cwd with env added and ended (its whole process group) past timeout
    seconds; parse(exit status, output) -> (passed, facts)."""
    return dict(name=name, cmd=[str(c) for c in cmd], cwd=Path(cwd), env=env or {}, parse=parse, timeout=timeout)


def shown(s):
    """A step's command as recorded: env first, the interpreter as python3, paths relative to the repository."""
    words = ['python3' if c == sys.executable else c for c in s['cmd']]
    words = [os.path.relpath(c, s['cwd']) if c.startswith(str(ROOT) + os.sep) else c for c in words]
    return shlex.join(['%s=%s' % kv for kv in s['env'].items()] + words)


def last_json(text):
    """The last JSON object printed at the start of a line (xyf_build.py's result), or None."""
    at = text.rfind('\n{')
    at = at + 1 if at >= 0 else (0 if text.startswith('{') else -1)
    try:
        return json.JSONDecoder().raw_decode(text, at)[0] if at >= 0 else None
    except ValueError:
        return None


def tests_parser(summary):
    def parse(status, _):
        try:
            counts = json.loads(Path(summary).read_text())
        except (OSError, ValueError):
            return False, {}
        return status == 0 and counts.get('failed') == 0 and counts.get('run', 0) > 0, dict(tests=counts)
    return parse


def verify_parser(out, tree, commit, test_builds):
    """--verify passed, and the candidate's bytes match the build's manifest. Then the build moves to
    BUILDS/<image>, and check.json there maps it to the tree and commit."""
    def parse(status, text):
        result, candidate = last_json(text), out / 'candidate.ldr'
        if status or not result or result.get('passed') is not True or not candidate.is_file():
            return False, {}
        data = candidate.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        try:
            recorded = json.loads((out / 'manifest.json').read_text()).get('candidate_sha256')
        except (OSError, ValueError):
            recorded = None
        if recorded != digest:
            return False, {}
        write_json(out / 'check.json', dict(image=digest[:8], tree=tree, commit=commit))
        # A second checkout can publish the same image bytes with different source hashes.
        shutil.copytree(out, test_builds / digest[:8])
        named = paths.name_by_image(out)
        return True, dict(candidate=dict(sha256=digest, image=named.name, bytes=len(data), build=str(named)))
    return parse


def touched(folder):
    """When folder or anything in it last changed (its newest mtime; 0 when it is gone)."""
    newest = 0
    for top, dirs, files in os.walk(folder):
        for name in [''] + files:
            try:
                newest = max(newest, os.stat(os.path.join(top, name)).st_mtime)
            except OSError:
                pass
    return newest


def fresh_build_folder(tree, now=None):
    """BUILDS/check-<tree>, emptied (and any other check-* left by a failed run); of the builds/<image>
    folders a check made, those beyond the KEEP_BUILDS - 1 newest are removed. A folder touched in the last
    RECENT seconds stays (other than this run's own): another run (a check in another worktree) may still be
    building it or reading it."""
    own, now = paths.BUILDS / ('check-' + tree[:12]), time.time() if now is None else now
    for folder in paths.BUILDS.glob('check-*'):
        if folder == own or now - touched(folder) > RECENT:
            shutil.rmtree(folder, ignore_errors=True)
    made = sorted((p for p in paths.BUILDS.glob('*/check.json') if paths.IMAGE_NAME.fullmatch(p.parent.name)),
                  key=lambda p: p.stat().st_mtime, reverse=True)
    for marker in made[KEEP_BUILDS - 1:]:
        if now - touched(marker.parent) > RECENT:
            shutil.rmtree(marker.parent, ignore_errors=True)
    return own


def standard_steps(args, root, tree, work):
    """The standard set for this run (work: a temporary folder for the suite's counts)."""
    py, summary = sys.executable, Path(work) / 'tests.json'
    npm = npm_steps(args, root)                  # before anything runs: a missing node_modules stops the run
    jobs = ['--jobs', str(args.jobs)] if args.jobs else []
    out = fresh_build_folder(tree)
    test_builds = Path(work) / 'builds'
    steps = [step('product build', [py, root / 'xyf_build.py', '--xyf-product', '--out', out], root),
             step('verify', [py, root / 'xyf_build.py', '--verify', '--out', out], root,
                  parse=verify_parser(out, tree, git(root, 'rev-parse', 'HEAD'), test_builds)),
             step('unit tests (exhaustive)', [py, root / 'scripts/run_tests.py', *jobs, '--summary', summary],
                  root, {'XYF_EXHAUSTIVE': '1', paths.CHECK_TEST_BUILDS: str(test_builds)}, tests_parser(summary),
                  timeout=SUITE_S)]
    if args.neutral is not None:
        ref = args.neutral or merge_base(root)
        if not ref:
            raise CheckError('--neutral: no main to compare with; name a REF')
        steps.append(step('neutral vs %s' % ref[:12], [py, root / 'scripts/neutral.py', ref], root))
    steps += npm
    return steps


NPM = (('clients/ts', ('clients/ts',), ()),      # folder, what changing reruns it, steps; clients/ts: no step,
       ('web', ('web', 'clients/ts'), (('npm test', ['test']), ('npm run typecheck', ['run', 'typecheck']))))
# its npm test runs inside the suite (NodeSuiteTest, whenever node_modules is there), so it is only required.


def npm_steps(args, root):
    """NPM's steps for each folder that changed (or all with --npm); CheckError when its node_modules is not
    installed."""
    steps, npm = [], shutil.which('npm') or 'npm'
    for rel, watched, commands in NPM:
        folder = root / rel
        if not folder.is_dir() or not (args.npm or changed(root, *watched)):
            continue
        if not (folder / 'node_modules').is_dir():
            raise CheckError('npm test needs %s/node_modules, which is missing: run `npm ci` in %s' % (rel, rel))
        steps += [step('%s (%s)' % (name, rel), [npm, *words], folder) for name, words in commands]
    return steps


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
                proc = procgroup.run(s['cmd'], cwd=s['cwd'], env=dict(os.environ, **s['env']), stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, text=True, errors='replace', timeout=s['timeout'])
                status, text = proc.returncode, proc.stdout
            except subprocess.TimeoutExpired as e:
                status, text = 124, (e.output or '') + '\nHUNG: %s ran past its %g s bound: ended\n' % (s['name'],
                                                                                                    s['timeout'])
            except OSError as e:
                status, text = 127, str(e)
            f.write(text)
            f.write('\n=== exit %d\n' % status)
            passed, found = s['parse'](status, text) if s['parse'] else (status == 0, {})
            passed = passed and status == 0
            facts.update(found)
            results.append(dict(name=s['name'], command=shown(s), result='pass' if passed else 'fail',
                                exit=status, seconds=round(time.monotonic() - start, 1)))
            print('  %-4s  %-24s %7.1f s' % (results[-1]['result'], s['name'], results[-1]['seconds']), flush=True)
            if not passed:
                break
    return results, facts


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


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
    log = Path(log or paths.SESSIONS / (datetime.date.today().isoformat() + '-check') /
               ('%s-%s.log' % (tree[:12], time.strftime('%H%M%S'))))
    started = now()
    steps = steps_for(tree)
    results, facts = run_steps(steps, log)
    require_clean(root, 'at end')
    if tree_of(root) != tree:
        raise CheckError('HEAD changed during the run: no receipt')
    passed = len(results) == len(steps) and all(r['result'] == 'pass' for r in results)
    record = dict(version=VERSION, passed=passed, tree=tree, commit=commit,
                  branch=git(root, 'rev-parse', '--abbrev-ref', 'HEAD'), started=started, finished=now(),
                  steps=results, tests=facts.get('tests'), candidate=facts.get('candidate'), log=str(log))
    receipt, failed = checks / (tree + '.json'), checks / (tree + '.failed.json')
    write_json(receipt if passed else failed, record)
    (failed if passed else receipt).unlink(missing_ok=True)
    return record


def one_run_at_a_time(lock, root, private=None):
    """Holds lock (an flock) until this process exits, first waiting, as long as it takes, for another check run
    holding it, saying so once. None when there is no private folder."""
    what = 'check.py in %s, branch %s' % (root, git(root, 'rev-parse', '--abbrev-ref', 'HEAD') or '-')
    return device_lock.lock(lock, None, private, what, 'check: waiting for another check run: %(who)s\n')


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
        raise CheckError('%s is receipt version %d, newer than this check.py reads (%d): update this checkout'
                         % (path, data['version'], VERSION))
    return data


def is_ancestor(root, commit, ref):
    return subprocess.run(['git', 'merge-base', '--is-ancestor', commit, ref], cwd=root,
                          capture_output=True, timeout=60).returncode == 0


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
    return (next((d for d in found if d.get('branch') == branch), None) or
            next((d for d in found if d.get('commit') and is_ancestor(root, d['commit'], ref)), None))


def confirm(root, ref='HEAD', checks=CHECKS):
    """The passing receipt for REF's tree; CheckError saying what is missing otherwise."""
    try:
        tree = tree_of(root, ref)
    except CheckError:
        raise CheckError('%s is not a commit here' % ref)
    receipt = load(checks / (tree + '.json'))
    if receipt is not None:
        if (receipt.get('passed') is not True or receipt.get('tree') != tree or not receipt.get('steps')
                or any(s.get('result') != 'pass' for s in receipt['steps'])):
            raise CheckError('failed receipt: %s does not record a pass for tree %s' % (checks / (tree + '.json'), tree))
        return receipt
    failed = load(checks / (tree + '.failed.json'))
    if failed is not None:
        bad = [s.get('name') for s in failed.get('steps', []) if s.get('result') != 'pass']
        raise CheckError('failed: the last check of %s (tree %s, %s) failed at %s; log %s' % (
            ref, tree[:12], failed.get('finished'), ', '.join(bad) or 'an unknown step', failed.get('log')))
    older = older_receipt(root, ref, checks)
    if older:
        raise CheckError('tree differs: %s has tree %s; the newest receipt for it covers tree %s (commit %s, %s). '
                         'Rerun python3 scripts/check.py on %s' % (ref, tree[:12], older['tree'][:12],
                                                                  older.get('commit', '?')[:12], older.get('finished'), ref))
    raise CheckError('no receipt for %s (tree %s): run python3 scripts/check.py on a clean checkout of it' % (ref, tree[:12]))


def describe(record):
    """What a receipt records, as printed lines."""
    lines = ['%s: tree %s, commit %s (%s), %s' % ('receipt' if record.get('passed') else 'FAILED check',
                                                  record['tree'], record['commit'][:12], record.get('branch'),
                                                  record.get('finished'))]
    for s in record['steps']:
        lines.append('  %-4s  %-24s %7.1f s  %s' % (s['result'], s['name'], s['seconds'], s['command']))
    t, c = record.get('tests'), record.get('candidate')
    if t:
        lines.append('tests: %d run, %d skipped, %d failed (%s s)' % (t['run'], t['skipped'], t['failed'], t.get('seconds')))
    if c:
        lines.append('candidate: image %s, sha256 %s, %d bytes, %s' % (c.get('image', c['sha256'][:8]), c['sha256'],
                                                                      c['bytes'], c['build']))
    lines.append('log: %s' % record.get('log'))
    return lines


def main(argv=None, root=ROOT, checks=CHECKS):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--confirm', nargs='?', const='HEAD', metavar='REF',
                        help="exit 0 only when a passing receipt matches REF's tree (default HEAD)")
    parser.add_argument('--jobs', type=int, help="unit-test processes at a time (default: run_tests.py's)")
    parser.add_argument('--neutral', nargs='?', const='', metavar='REF',
                        help='also scripts/neutral.py REF (default REF: the merge base with origin/main, else main)')
    parser.add_argument('--npm', action='store_true', help='the web npm steps and the clients/ts node_modules check even when unchanged')
    args = parser.parse_args(argv)
    test_run = no_device.on()                    # a test run never waits for, nor blocks, a real check run
    no_device.switch_on()                        # every step and its subprocesses: no real device
    try:
        if args.confirm:
            print('\n'.join(describe(confirm(root, args.confirm, checks))))
            return 0
        if not test_run:
            one_run_at_a_time(checks / 'check.lock', root)
        paths.SESSIONS.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.xyf-check-', dir=paths.SESSIONS) as work:
            record = check(root, lambda tree: standard_steps(args, root, tree, work), checks)
    except CheckError as e:
        print('check: %s' % e, file=sys.stderr)
        return 1
    print('\n'.join(describe(record)))
    print('%s: %s' % ('receipt' if record['passed'] else 'no receipt; failure recorded in',
                      checks / (record['tree'] + ('.json' if record['passed'] else '.failed.json'))))
    return 0 if record['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
