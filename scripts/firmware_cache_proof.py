#!/usr/bin/env python3
"""Proves the firmware cache's hit and miss cases (scripts/firmware_cache.py) on a scratch worktree of HEAD.

    uv run scripts/firmware_cache_proof.py [--keep]      (mise run firmware-cache-proof; two real builds; never the OP-XY)

The cases, in order: a fresh build misses; unchanged, a hit restores the same bytes; one source changed misses and
rebuilds, and a hit then restores that build; a --verify failure is not cached (it misses again); reverted, a hit
restores the first build's bytes; a comment outside the sources hits; changed stock files miss (the build refuses
them, and that failure is not cached either); a moved toolchain or changed pins miss. It needs the stock pair, the
toolchain and pnpm's store (`pnpm install` here). The worktree and its files go in
<private>/xy-forever/sessions/<date>-cache-proof/, removed after (--keep leaves them).
"""

import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from xy_forever import paths

ROOT = Path(__file__).resolve().parents[1]

TURBO = Path('node_modules/.bin/turbo')  # the scratch tree's own (pnpm run would relink a shared one)
CHANGED = 'target/ui_gfx.cpp'  # a build source
OUTSIDE = 'scripts/check.py'  # not one


def digest(data):
    return hashlib.sha256(data).hexdigest()


def snapshot(tree):
    """{path: SHA-256} of build/product."""
    out = tree / 'build/product'
    return {str(p.relative_to(out)): digest(p.read_bytes()) for p in sorted(out.rglob('*')) if p.is_file()}


class Proof:
    def __init__(self, tree, env):
        self.tree, self.env, self.rows = tree, env, []

    def run(self, *argv, env=None):
        """(exit status, turbo's cache statuses in order, seconds) of a command in the scratch tree."""
        start = time.monotonic()
        proc = subprocess.run(
            [str(a) for a in argv],
            cwd=self.tree,
            env=dict(self.env, **(env or {})),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=300,
        )
        return proc.returncode, re.findall(r'^cache (hit|miss)', proc.stdout, re.M), time.monotonic() - start

    def cached(self, task, env=None):
        return self.run('uv', 'run', '--frozen', 'scripts/firmware_cache.py', task, env=env)

    def dry_status(self, env):
        """The build task's cache status (HIT or MISS) in a dry run under env: nothing is built."""
        key = subprocess.run(
            ['uv', 'run', '--frozen', 'scripts/firmware_cache.py', 'key'],
            cwd=self.tree,
            env=dict(self.env, **env),
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        run = subprocess.run(
            [str(self.tree / TURBO), 'run', 'firmware-build', '--dry=json'],
            cwd=self.tree,
            check=True,
            env=dict(self.env, **env, XYF_BUILD_KEY=key),
            capture_output=True,
            text=True,
        )
        return json.loads(run.stdout)['tasks'][0]['cache']['status']

    def record(self, case, ok, detail):
        self.rows.append((ok, case, detail))
        print('  %-4s  %-46s %s' % ('ok' if ok else 'FAIL', case, detail), flush=True)

    def build_case(self, case, expect, same_as=None, fails=False, env=None):
        shutil.rmtree(self.tree / 'build/product', ignore_errors=True)
        status, seen, seconds = self.cached('build', env)
        ok = seen == [expect] and (status != 0) == fails
        snap = snapshot(self.tree) if status == 0 else None
        if same_as is not None:
            ok = ok and snap == same_as
        self.record(
            case,
            ok,
            '%s, exit %d, %.1f s%s'
            % ('/'.join(seen) or 'no task', status, seconds, ', bytes identical' if same_as is not None and ok else ''),
        )
        return snap


def edit(path, text):
    old = path.read_bytes()
    path.write_bytes(old + text.encode())
    return old


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    session = paths.SESSIONS / (datetime.date.today().isoformat() + '-cache-proof')
    tree, private = session / 'tree', session / 'private'
    shutil.rmtree(session, ignore_errors=True)
    subprocess.run(
        ['git', 'worktree', 'add', '--force', '--detach', '--quiet', str(tree), 'HEAD'], cwd=ROOT, check=True
    )
    subprocess.run(
        ['pnpm', 'install', '--frozen-lockfile', '--offline', '--silent'], cwd=tree, check=True, timeout=300
    )  # from the store pnpm install filled for this checkout
    env = {k: v for k, v in os.environ.items() if k not in ('VIRTUAL_ENV', 'XYF_BUILD_KEY')}
    proof = Proof(tree, dict(env, TURBO_TELEMETRY_DISABLED='1'))
    try:
        first = proof.build_case('fresh tree: miss', 'miss')
        proof.build_case('unchanged: hit, same bytes', 'hit', first)

        original = edit(tree / CHANGED, '\n// cache proof\n')
        changed = proof.build_case('%s changed: miss, rebuilt' % CHANGED, 'miss')
        manifest = json.loads((tree / 'build/product/manifest.json').read_text())
        proof.record(
            '  ... the rebuild hashed the changed file',
            changed != first and manifest['source_sha256'][CHANGED] == digest((tree / CHANGED).read_bytes()),
            'manifest',
        )
        proof.build_case('changed, again: hit, same bytes as its build', 'hit', changed)

        candidate = tree / 'build/product/candidate.ldr'
        candidate.write_bytes(b'\0' + candidate.read_bytes()[1:])
        key = subprocess.run(
            ['uv', 'run', '--frozen', 'scripts/firmware_cache.py', 'key'],
            cwd=tree,
            env=proof.env,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        for n in (1, 2):
            status, seen, seconds = proof.run(
                tree / TURBO,
                'run',
                'firmware-verify',
                '--only',
                '--cache=local:rw',
                '--ui=stream',
                '--log-prefix=none',
                env=dict(XYF_BUILD_KEY=key),
            )
            proof.record(
                'tampered candidate, verify run %d: miss, fails' % n,
                seen == ['miss'] and status != 0,
                '%s, exit %d, %.1f s' % ('/'.join(seen), status, seconds),
            )
        status, seen, seconds = proof.cached('verify')
        proof.record(
            'verify: build hit restores, verify passes',
            seen == ['hit', 'miss'] and status == 0 and snapshot(tree) == changed,
            '%s, exit %d, %.1f s' % ('/'.join(seen), status, seconds),
        )

        (tree / CHANGED).write_bytes(original)
        proof.build_case("reverted: hit, the first build's bytes", 'hit', first)
        original = edit(tree / OUTSIDE, '\n# cache proof\n')
        proof.build_case('comment in %s: hit, same bytes' % OUTSIDE, 'hit', first)
        (tree / OUTSIDE).write_bytes(original)

        stock = private / 'firmware/stock-1.2.1'
        stock.mkdir(parents=True)
        shutil.copy2(paths.STOCK_121 / 'bootloader.ldr', stock)
        data = bytearray((paths.STOCK_121 / 'os.ldr').read_bytes())
        data[-1] ^= 1
        (stock / 'os.ldr').write_bytes(bytes(data))
        for n in (1, 2):
            proof.build_case(
                'stock os.ldr changed, run %d: miss, refused' % n,
                'miss',
                fails=True,
                env=dict(XYF_PRIVATE=str(private)),
            )
        proof.record(
            'toolchain moved: miss (dry run)',
            proof.dry_status(dict(XYF_TOOLCHAIN=str(private / 'tc'))) == 'MISS',
            'XYF_TOOLCHAIN elsewhere',
        )
        original = edit(tree / 'toolchains.json', '\n')
        proof.record('toolchain pins changed: miss (dry run)', proof.dry_status({}) == 'MISS', 'toolchains.json')
        (tree / 'toolchains.json').write_bytes(original)
        proof.record('restored tree: hit (dry run)', proof.dry_status({}) == 'HIT', 'as at the start')
    finally:
        if '--keep' not in argv:
            subprocess.run(['git', 'worktree', 'remove', '--force', str(tree)], cwd=ROOT)
            shutil.rmtree(session, ignore_errors=True)
    failed = [case for ok, case, _ in proof.rows if not ok]
    print('firmware_cache_proof: %s' % ('every case holds' if not failed else 'FAILED: ' + '; '.join(failed)))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
