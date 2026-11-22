# Operating the device: build, verify, install

Part of [Operating the device](../OPERATING.md).

The rules in [SAFETY.md](../SAFETY.md) are binding for everything here.

## Build, verify, install (every build)

```sh
cd xy-forever        # toolchain: ../tools/blackfin (elsewhere: docs/operating/dev-tools.md)
mise run doctor                  # stock pair + fixtures in place? (first: operating/first-install.md)
mise run check                   # lint, build, --verify, every tier but the sweeps: the build lands in builds/<image>
mise run check --confirm         # this commit's check receipt, with its image
uv run xyf-build --xyf-product --out ../private/xy-forever/builds/repeat   # by hand: same candidate SHA
uv run xyf-build --verify --out ../private/xy-forever/builds/repeat        # {"passed": true}
uv run scripts/device_pass.py --plan --install ../private/xy-forever/builds/IMAGE  # inspect the qualification plan
```

`--xyf-product` is the product (boot model v3) and the only installable mode (the
v2 two-session flow was removed); a device test build (`--xyf-crash-trigger`) installs only for its own test.
Your status notes name the installed and known-good builds. Builds are deterministic, but `--verify` from a later checkout
fails with "Source changed since build": re-verify an old build from its own commit.
Only the install's verify reply proves an install; a later successful boot does not.

A build is named by its image: the first 8 hex digits of its candidate's SHA-256 (`builds/<image>/`,
`xy_forever/paths.py name_by_image`; HELLO `image=` on the device, the Info page). Nothing is bumped
per build: the same source gives the same image, and a doc-only commit leaves it unchanged. The
release version (`xy_forever/release.py`, from `0.1.0`; HELLO `version=`, the menu's corner) changes
only in a release pull request. `check.py`'s receipt and `builds/<image>/check.json` map an image to
its commit and tree. Delete the repeat build once its SHA matches. Prepare a dev-menu kit (copy
`candidate.ldr` to `../private/xy-forever/recovery/known-good-<image>/os.ldr`) once a build has proven
itself on the device.

A change that should not alter the firmware (refactor, comments, dead code): run
`mise run neutral [REF]` instead. It builds every mode from `REF`
(default `HEAD`) and from the working tree and compares the candidates. Its
builds go to `../private/xy-forever/sessions/neutral/`.

The device pass turns on no watchdog: there is none (protocol 1.14). `--plan` and simulator runs work
offline. Every hardware qualification still needs the checks and approvals in [SAFETY.md](../SAFETY.md).

## Checks

```sh
mise run test                    # the fast tier (about 2 s); a file, node id, -k or -m: any tier
mise run test-changed            # after each change: the tests it affects (testmon), sweeps aside
mise run test-one TARGET         # one file, class or test (tests/test_aes.py::NativeAesTests)
mise run test-failed             # what failed last time (nothing failed: nothing runs)
mise run check                   # once per PR: the gate, under 30 s
mise run test-all                # every tier but the sweeps (about 11 s); check and CI run it
mise run test-sweeps             # the five long simulator sweeps (about 8 s)
mise run lint                    # ruff, ESLint, Prettier, clang-format; format, lint-fix: apply
```

The pre-commit hook (`lefthook.yml`, installed by `mise run setup`) formats and lints the staged files (about a
second); a finding stops the commit.

The suite runs under pytest (`pyproject.toml`; tiers, budgets, plugins: `tests/conftest.py`), `-n auto`. Each test is in a tier: `fast` (the default), `gate` (slower: sanitizer builds,
fuzzing, the published kit, the short simulator sweeps), `sim` (the simulator core, each test saying why it is
kept) or `sweep` (five 3-9 s chains of simulator restarts: they doubled a shared run, so only `test-sweeps`).

Budgets are enforced on CPU time everywhere, CI too: one test's and each tier's sum (`BUDGETS`, e.g. fast 0.3 s
a test). A test's CPU time is its phases in its worker plus the child processes it waited for (a simulator it
stopped); the host C++ is built before the run, never in a test. Going over fails the run, naming the test or tier: make it faster or move
it a tier. A busy machine stretches wall time, not CPU time; a probe raises the limits on slower hardware, and
`--testmon` by its tracing. Wall time is only reported. Every test is also bounded, to end a hang, never a slow run (`bounds.TEST_S`, `SIM_TEST_S`): past it
the run prints HUNG with every thread's traceback and its worker's process group ends. `test-changed` adds to testmon's Python tracking
the C++ each test used (a `hosttest/` module, doctest file or program and its includes) and files it runs as a
subprocess (`@pytest.mark.uses('scripts/x.py')`).

All host C++ (the tests', the host tools', the simulator's) is one CMake build: [host C++](host-build.md).

`mise run check` (`scripts/check.py`) is the PR gate on a clean, committed tree: the product build into
`build/product` plus `--verify` (cached, below; a verified build is copied to `../private/xy-forever/builds/<image>`),
`protogen --check`, `mise run lint`, `pnpm run typecheck` in `clients/ts` and `web` when they differ from the
merge base with `origin/main` (or with `--js`), then one pytest run of every tier but the sweeps with a temporary
copy of the verified build. A missing or stale `node_modules` (a fresh worktree) gets `pnpm install` first;
`mise run build` does the same. A step over 15 s, or the whole over 30 s, is reported, never failed. Step output goes to a log in
`../private/xy-forever/sessions/<date>-check/`. A passing run writes the receipt
`../private/xy-forever/checks/<tree>.json` (`git rev-parse HEAD^{tree}`): a cache, so a rerun on the same tree
stops at once (`--force` reruns); `--confirm [REF]` prints what it records. `mise run neutral [REF]`
(opt-in) compares every build mode with REF: a clean tree against HEAD is "same" at once; the working tree's
product build comes through the firmware cache, its other modes are cached per clean tree, REF's per commit. CI
(`.github/workflows/checks.yml`: `mise run test-all` without private files, with open-xy's public toolchain from
`setup.py toolchain`; lint, the web and client suites) is the merge check. Neither stands in for a device test. Firmware may also be built into the checkout's gitignored
`build/` folder; TE bytes are never committed or uploaded.

`mise run build`, `mise run verify` and the check's build steps go through Turborepo's local cache
(`scripts/firmware_cache.py`, `turbo.json`). The key is every file `xyf-build` hashes as its sources, plus the
SHA-256 of the stock pair, the toolchain's location and pins, the Python running it and the checkout's path,
computed on every run. With all of them unchanged, `build/product` and the `--verify` log are restored in about
0.2 s; anything else rebuilds. A failed build or verify is never cached. The cache is `.turbo/cache` in the
checkout (gitignored, at most 1 GB, never uploaded: remote caching is off). `--force` (`mise run build --force`)
builds past it; deleting `.turbo/` clears it. `scripts/firmware_cache_proof.py` re-proves the hit and miss cases.

Tests never open a real device: `tests/conftest.py`, `check.py` and any process that loads `unittest` set
`XYF_NO_DEVICE` for themselves and every process they start, and with it every real-device opener (the serial
port, USB MTP, pyusb; `xy_forever/no_device.py`) raises "tests may not open a real device" before it looks for
one. There is no opt-out: a test fakes the opener, or uses the simulator (TCP) or a pty. The browser tests run
Chrome without Web Serial.
