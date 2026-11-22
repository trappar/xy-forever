#!/usr/bin/env python3
"""Keep the private folder's sessions and builds from growing without end: a dry run unless --yes.

    uv run scripts/clean.py [--keep N] [--yes]      (mise run clean [--keep N] [--yes])

In <private>/xy-forever/sessions/ and builds/ it keeps the newest N entries of each (default 10; newest by
the latest mtime of the entry or its direct children) and lists the rest with their sizes; --yes deletes them.
Never deleted, whatever their age: PROTECTED (the build the device runs today), any entry named in a local
status file (docs/status/*.md of the main checkout: what it records as installed or known-good), the newest
verified build of this source (tests use it), and anything touched in the last RECENT seconds (a running
session or check; it counts among the newest). Only sessions/ and builds/ are looked at: recovery/, checks/,
fixtures and every other private folder stay as they are. Symbolic links are removed as links, never followed.
"""
import argparse
from pathlib import Path
import re
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from xy_forever import paths  # noqa: E402

KEEP = 10
PROTECTED = {'2a96c724'}           # builds/: the known-good image installed on the device (never deleted)
RECENT = 60 * 60                   # seconds: an entry touched since then is in use


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


def plan(folder, keep, protected, now=None):
    """(kept, removed): the entries of folder, newest first; removed: unprotected, not recent and past the newest
    `keep` unprotected ones."""
    now = time.time() if now is None else now
    if not folder.is_dir():
        return [], []
    entries = sorted(folder.iterdir(), key=newest_mtime, reverse=True)
    kept, removed, counted = [], [], 0
    for entry in entries:                    # a protected entry takes none of the `keep` places
        counted += entry.name not in protected
        spare = entry.name in protected or counted <= keep or now - newest_mtime(entry) < RECENT
        (kept if spare else removed).append(entry)
    return kept, removed


def remove(entry):
    if entry.is_dir() and not entry.is_symlink():
        shutil.rmtree(entry)
    else:
        entry.unlink()


def main(argv=None, xyf=None, status=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--keep', type=int, default=KEEP, help='entries to keep in each folder (default %(default)s)')
    parser.add_argument('--yes', action='store_true', help='delete; without it, only list what would go')
    args = parser.parse_args(argv)
    if args.keep < 1:
        parser.error('--keep must be 1 or more')
    xyf = Path(xyf or paths.XYF)
    protected = PROTECTED | named_in_status(status or paths.STATUS)
    if xyf == paths.XYF:
        newest = paths.newest_verified_build()
        if newest:
            protected.add(newest.name)
    freed = 0
    for name in ('sessions', 'builds'):
        kept, removed = plan(xyf / name, args.keep, protected)
        print('%s/: %d entries, keeping %d, %s %d' % (xyf / name, len(kept) + len(removed), len(kept),
                                                     'removing' if args.yes else 'would remove', len(removed)))
        for entry in removed:
            n = size(entry)
            freed += n
            print('  %s %-50s %8.1f MB' % ('-' if args.yes else '?', entry.name, n / 1e6))
            if args.yes:
                remove(entry)
    print('%s %.1f GB%s' % ('freed' if args.yes else 'would free', freed / 1e9,
                            '' if args.yes else ' (dry run: nothing changed; --yes deletes)'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
