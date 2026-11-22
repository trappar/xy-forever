#!/usr/bin/env python3
"""Check that the working tree builds the same firmware as a git ref.

    python3 scripts/neutral.py [REF]        (default REF: HEAD)

Builds every mode from REF (a clean export, cached per commit) and from the
working tree, then compares the candidate SHA-256 of each mode. A change meant
to be behaviour-neutral must leave every candidate unchanged. Exit status: 0
when all match, 1 otherwise. A build whose own verification fails is reported
but still compared. A mode that REF cannot build (a mode added since) is shown as
"new" and not compared. Build outputs go to <private>/xy-forever/sessions/neutral/
(<private>/xy-forever/sessions/neutral/<worktree name>/ for a worktree not named xy-forever)
(the working tree's and the last REF's; older ones are removed), so a difference
can be traced by comparing the two module.asm files. No device is involved.

Opt-in (mise run neutral [REF]): on a clean tree the working tree's product build is the one scripts/check.py
verified for that tree (builds/<image>/check.json names it), when there is one, instead of a new build.
"""

import argparse
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

from xy_forever.paths import BUILDS, PRIVATE_ROOT, SESSIONS
from xy_forever.toolchain import authenticate
from xy_forever.xyf_build import MODES

ROOT = Path(__file__).resolve().parents[1]


def git(*args):
    return subprocess.run(['git', *args], cwd=ROOT, check=True, capture_output=True).stdout


def build(source, out, env):
    """Build one mode into out; returns (candidate sha or None, 'ok' or the last error line)."""
    mode = out.name
    if out.exists():
        shutil.rmtree(out)
    run = subprocess.run(
        [sys.executable, '-m', 'xy_forever.xyf_build', '--' + mode, '--out', str(out)],  # cwd: source's package first
        cwd=source,
        env=env,
        capture_output=True,
        text=True,
    )
    candidate = out / 'candidate.ldr'
    digest = hashlib.sha256(candidate.read_bytes()).hexdigest() if candidate.is_file() else None
    return digest, 'ok' if run.returncode == 0 else (run.stderr.strip().splitlines() or ['failed'])[-1]


def result_of(out):
    """A finished build's recorded result (written by build_all), or None."""
    note = out / 'neutral.txt'
    return tuple(note.read_text().split('\n', 1)) if note.is_file() else None


def checked_product(tree):
    """(candidate sha, 'ok (the check's build)') of the product build check.py verified for tree, or None."""
    for marker in BUILDS.glob('*/check.json'):
        try:
            if json.loads(marker.read_text()).get('tree') == tree:
                return hashlib.sha256(
                    (marker.parent / 'candidate.ldr').read_bytes()
                ).hexdigest(), "ok (the check's build)"
        except OSError, ValueError:
            continue
    return None


def build_all(source, base, env, cached=False, known=None):
    results = {}
    for mode in MODES:
        out = base / mode
        results[mode] = (known or {}).get(mode) or (cached and result_of(out)) or None
        if results[mode] is None:
            digest, status = build(source, out, env)
            out.mkdir(parents=True, exist_ok=True)  # a build that refused its mode made no folder
            (out / 'neutral.txt').write_text((digest or '-') + '\n' + status)
            results[mode] = (digest or '-', status)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('ref', nargs='?', default='HEAD', help='git ref to compare with (default: HEAD)')
    args = parser.parse_args()
    commit = git('rev-parse', '--verify', args.ref + '^{commit}').decode().strip()
    compiler, _ = authenticate()
    env = dict(os.environ, XYF_PRIVATE=str(PRIVATE_ROOT), XYF_TOOLCHAIN=str(compiler['root']))
    work = SESSIONS / 'neutral'
    if ROOT.name != 'xy-forever':  # a separate git worktree gets its own folder
        work = work / ROOT.name
    for old in work.glob('*'):  # keep one ref's builds (about 50 MB each)
        if old.is_dir() and old.name not in (commit[:12], 'worktree') and not old.name.startswith('xyf-'):
            shutil.rmtree(old)
    with tempfile.TemporaryDirectory() as tmp:
        tarfile.open(fileobj=io.BytesIO(git('archive', commit))).extractall(tmp)
        before = build_all(Path(tmp), work / commit[:12], env, cached=True)
    clean = not git('status', '--porcelain').strip()
    product = clean and checked_product(git('rev-parse', 'HEAD^{tree}').decode().strip())
    after = build_all(ROOT, work / 'worktree', env, known={'xyf-product': product} if product else None)
    same = True
    print('%-16s %-14s %-14s %s' % ('mode', args.ref[:14], 'working tree', 'result'))
    for mode in MODES:
        (old, old_status), (new, new_status) = before[mode], after[mode]
        added = old == '-' and new != '-'  # REF has no such candidate: nothing to compare
        match = old == new and old != '-'
        same = same and (match or added)
        print('%-16s %-14s %-14s %s' % (mode, old[:12], new[:12], 'new' if added else 'same' if match else 'DIFFERENT'))
        # A build that fails its own verification still leaves a candidate: say so.
        if old_status != 'ok':
            print('    %s build: %s' % (args.ref, old_status))
        if not new_status.startswith('ok'):
            print('    working tree build: ' + new_status)
    print(
        'neutral: every candidate REF also builds is unchanged'
        if same
        else 'NOT neutral: compare %s/<mode>/module.asm with %s/<mode>/module.asm'
        % (work / commit[:12], work / 'worktree')
    )
    return 0 if same else 1


if __name__ == '__main__':
    sys.exit(main())
