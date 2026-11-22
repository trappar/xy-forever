# Checks

Part of [Operating the device](../OPERATING.md). Every change runs them; none stands in for a device test.

## Commands

```sh
mise run test                    # the fast tier; a file, node id, -k or -m: any tier
mise run test-changed            # after each change: the tests it affects (testmon), sweeps aside
mise run test-one TARGET         # one file, class or test (tests/test_aes.py::NativeAesTests)
mise run test-failed             # what failed last time (nothing failed: nothing runs)
mise run check                   # once per PR: the gate
mise run test-all                # every tier but the sweeps; check and CI run it
mise run test-sweeps             # the long simulator sweeps
mise run lint                    # ruff, vulture, ESLint, Prettier, knip, clang-format; format, lint-fix: apply
```

How long each takes lives in one place: `mise tasks` (the task descriptions, measured). The docs never
repeat those figures.

The pre-commit hook (`lefthook.yml`, installed by `mise run setup`) formats and lints the staged files; a
finding stops the commit.

## Tiers and budgets

The suite runs under pytest (`pyproject.toml`; tiers, budgets, plugins: `tests/conftest.py`), `-n auto`. Each
test is in a tier: `fast` (the default), `gate` (slower: sanitizer builds, fuzzing, the published kit, the
short simulator sweeps), `sim` (the simulator core, each test saying why it is kept) or `sweep` (chains of
simulator restarts: they doubled a shared run, so only `test-sweeps`).

Budgets are enforced on CPU time everywhere, CI too: one test's and each tier's sum (`BUDGETS` in
`tests/conftest.py`, the one place they live). A test's CPU time is its phases in its worker plus the child
processes it waited for (a simulator it stopped); the host C++ is built before the run, never in a test. Going
over fails the run, naming the test or tier: make it faster or move it a tier. A busy machine stretches wall
time, not CPU time; a probe raises the limits on slower hardware, and `--testmon` by its tracing. Wall time is
only reported. Every test is also bounded, to end a hang, never a slow run (`xy_forever/bounds.py`): past it
the run prints HUNG with every thread's traceback and its worker's process group ends. `test-changed` adds to
testmon's Python tracking the C++ each test used (a `hosttest/` module, doctest file or program and its
includes) and files it runs as a subprocess (`@pytest.mark.uses('scripts/x.py')`).

All host C++ (the tests', the host tools', the simulator's) is one CMake build: [host C++](host-build.md).

## The PR gate

`mise run check` (`scripts/check.py`) is the PR gate on a clean, committed tree: the product build into
`build/product` plus `--verify` (cached, below; a verified build is copied to
`../private/xy-forever/builds/<image>`), `protogen --check`, `mise run lint`, the TypeScript checks in
`clients/ts` and `web` when they differ from the merge base with `origin/main` (or with `--js`), then one
pytest run of every tier but the sweeps. A missing or stale `node_modules` (a fresh worktree) gets
`pnpm install` first; `mise run build` does the same. A slow step or run is reported against check.py's
wall figures, never failed. Step output goes to a log in `../private/xy-forever/sessions/<date>-check/`. A
passing run writes the receipt `../private/xy-forever/checks/<tree>.json` (`git rev-parse HEAD^{tree}`): a
cache, so a rerun on the same tree stops at once (`--force` reruns); `--confirm [REF]` prints what it records.

Old builds are pruned in one place, `scripts/clean.py` (`mise run clean`: sessions and builds, a dry run
unless `--yes`); check runs its builds-only pass after a receipt. Never pruned: the known-good build, anything
your status notes name, the newest verified build of each checkout's sources, anything touched in the last
hour.

`mise run neutral [REF]` (opt-in) compares every build mode with REF: a clean tree against HEAD is "same" at
once; the working tree's product build comes through the firmware cache, its other modes are cached per clean
tree, REF's per commit. CI (`.github/workflows/checks.yml`, on Linux: `mise run test-all` without private files
or the Blackfin toolchain, whose tests only `check` or a manual macOS run cover; lint, the web and client suites;
the browser tests when the web app changes) is the merge check, on pull requests only.
Firmware may also be built into the checkout's gitignored `build/` folder; TE bytes are never committed or
uploaded.

## Firmware cache

`mise run build`, `mise run verify` and the check's build steps go through Turborepo's local cache
(`scripts/firmware_cache.py`, `turbo.json`). The build's key is every file `xyf-build` hashes as its sources,
plus the SHA-256 of the stock pair, the toolchain's location and pins, the Python running it and the
checkout's path, computed on every run; verify's key is the build's (Turborepo hashes a task's dependencies
into its own). With all of them unchanged, `build/product` and the `--verify` log are restored; anything else
rebuilds. A failed build or verify is never cached. The cache is `.turbo/cache` in the checkout (gitignored,
at most 1 GB, never uploaded: remote caching is off). `--force` (`mise run build --force`) builds past it;
deleting `.turbo/` clears it. `scripts/firmware_cache_proof.py` re-proves the hit and miss cases.

## No real device

Tests never open a real device: `tests/conftest.py`, `check.py` and any process that loads `unittest` set
`XYF_NO_DEVICE` for themselves and every process they start, and with it every real-device opener (the serial
port, USB MTP, pyusb; `xy_forever/no_device.py`) raises "tests may not open a real device" before it looks for
one. There is no opt-out: a test fakes the opener, or uses the simulator (TCP) or a pty. The browser tests run
Chrome without Web Serial.
