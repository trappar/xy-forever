#!/usr/bin/env python3
"""The one place that prunes the private folder's sessions and builds: a dry run unless --yes.

    uv run scripts/clean.py [--keep N] [--builds-only] [--yes]      (mise run clean [--keep N] [--yes])

In <private>/xy-forever/sessions/ and builds/ it keeps the newest N entries of each (default 10; newest by
the latest mtime of the entry or its direct children) and lists the rest with their sizes; --yes deletes them.
Never deleted, whatever their age: PROTECTED (the known-good build the device runs), any entry named in a local
status note (<private>/xy-forever/status/*.md: what it records as installed or known-good), the newest verified
build of each checkout's sources (every git worktree of this repository: tests use it), and anything touched in
the last RECENT seconds (a running session or check; it counts among the newest). Only sessions/ and builds/ are
looked at: recovery/, checks/, fixtures and every other private folder stay as they are. Symbolic links are
removed as links, never followed.

prune_builds() (--builds-only --yes) is the automatic run, after scripts/check.py records a receipt: builds/
only, with the same rules; sessions/ are pruned only on request.
"""

import argparse
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from xy_forever import paths, xyf_build
from xy_forever.build_modules import own_sources
from xy_forever.compile import sha

ROOT = Path(__file__).resolve().parents[1]

KEEP = 10
PROTECTED = {'2a96c724'}  # builds/: the known-good image installed on the device (never deleted)
RECENT = 60 * 60  # seconds: an entry touched since then is in use


def newest_mtime(path):
    times = [path.lstat().st_mtime]
    if path.is_dir() and not path.is_symlink():
        try:
            times += [child.lstat().st_mtime for child in path.iterdir()]
        except OSError:
            pass
    return max(times)


def size(path):
    if path.is_symlink() or not path.is_dir():
        return path.lstat().st_size
    total = 0
    for p in path.rglob('*'):
        try:
            total += p.lstat().st_size
        except OSError:
            pass
    return total


def named_in_status(status=paths.STATUS):
    """Every word of the local status files (build names among them)."""
    words = set()
    for f in sorted(Path(status).glob('**/*.md')) if Path(status).is_dir() else []:
        words.update(re.findall(r'[\w.-]+', f.read_text(errors='replace')))
    return words


def checkouts(root=ROOT):
    """Every checkout of this repository that exists (git worktree list), else root alone."""
    try:
        out = subprocess.run(
            ['git', 'worktree', 'list', '--porcelain'], cwd=root, capture_output=True, text=True, check=True
        ).stdout
    except OSError, subprocess.CalledProcessError:
        return [root]
    trees = [Path(line[len('worktree ') :]) for line in out.splitlines() if line.startswith('worktree ')]
    return [t for t in trees if (t / 'target').is_dir()] or [root]


def source_hashes(tree):
    """{source: SHA-256} of a checkout, as xyf_build.py source_hashes() computes it for its own."""
    return {name: sha((tree / name).read_bytes()) for name in own_sources(tree, xyf_build.SOURCE_SUFFIXES)}


def newest_per_checkout(builds, trees):
    """Names of the newest verified build (paths.build_image) of each checkout's sources. A checkout whose
    sources cannot be read (a branch switch midway) protects every verified build: never guess."""
    verified = []
    for d in builds.glob('*') if builds.is_dir() else []:
        named = paths.build_image(d)
        if named:
            verified.append(((d / 'verification.json').stat().st_mtime, d.name, named[1].get('source_sha256')))
    keep = set()
    for tree in trees:
        try:
            source = source_hashes(tree)
        except OSError:
            return {name for _, name, _ in verified}
        mine = [(t, name) for t, name, s in verified if s == source]
        if mine:
            keep.add(max(mine)[1])
    return keep


def protected_names(xyf, status, trees=None):
    names = PROTECTED | named_in_status(status)
    return names | newest_per_checkout(xyf / 'builds', checkouts() if trees is None else trees)


def plan(folder, keep, protected, now=None):
    """(kept, removed): the entries of folder, newest first; removed: unprotected, not recent and past the newest
    `keep` unprotected ones."""
    now = time.time() if now is None else now
    if not folder.is_dir():
        return [], []
    entries = sorted(folder.iterdir(), key=newest_mtime, reverse=True)
    kept, removed, counted = [], [], 0
    for entry in entries:  # a protected entry takes none of the `keep` places
        counted += entry.name not in protected
        spare = entry.name in protected or counted <= keep or now - newest_mtime(entry) < RECENT
        (kept if spare else removed).append(entry)
    return kept, removed


def remove(entry):
    if entry.is_dir() and not entry.is_symlink():
        shutil.rmtree(entry)
    else:
        entry.unlink()


def prune(folders, xyf, keep, delete, protected, listing=True):
    """Print each folder's plan (listing: every entry it removes too); with delete, remove what it lists. The bytes
    freed (or that would be)."""
    freed = 0
    for name in folders:
        kept, removed = plan(xyf / name, keep, protected)
        print(
            '%s/: %d entries, keeping %d, %s %d'
            % (xyf / name, len(kept) + len(removed), len(kept), 'removing' if delete else 'would remove', len(removed))
        )
        for entry in removed:
            n = size(entry)
            freed += n
            if listing:
                print('  %s %-50s %8.1f MB' % ('-' if delete else '?', entry.name, n / 1e6))
            if delete:
                remove(entry)
    return freed


def prune_builds(xyf=None, status=None, keep=KEEP, trees=None):
    """The automatic run (scripts/check.py, after its receipt): builds/ only, deleting, one line printed. The bytes
    freed."""
    xyf = Path(xyf or paths.XYF)
    return prune(['builds'], xyf, keep, True, protected_names(xyf, status or paths.STATUS, trees), listing=False)


def main(argv=None, xyf=None, status=None, trees=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--keep', type=int, default=KEEP, help='entries to keep in each folder (default %(default)s)')
    parser.add_argument('--builds-only', action='store_true', help='builds/ only; sessions/ stay')
    parser.add_argument('--yes', action='store_true', help='delete; without it, only list what would go')
    args = parser.parse_args(argv)
    if args.keep < 1:
        parser.error('--keep must be 1 or more')
    xyf = Path(xyf or paths.XYF)
    protected = protected_names(xyf, status or paths.STATUS, trees)
    folders = ['builds'] if args.builds_only else ['sessions', 'builds']
    freed = prune(folders, xyf, args.keep, args.yes, protected)
    print(
        '%s %.1f GB%s'
        % (
            'freed' if args.yes else 'would free',
            freed / 1e9,
            '' if args.yes else ' (dry run: nothing changed; --yes deletes)',
        )
    )
    return 0


if __name__ == '__main__':
    sys.exit(main())
