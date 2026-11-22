# Operating the device: build, verify, install

Part of [Operating the device](../OPERATING.md).

The rules in [SAFETY.md](../SAFETY.md) are binding for everything here.

## Build, verify, install (every build)

```sh
cd xy-forever        # toolchain: ../tools/blackfin (or set XYF_TOOLCHAIN)
mise run doctor                  # stock pair + fixtures in place? (first: operating/first-install.md)
mise run check                   # build, --verify, fast and gate tiers: the build lands in builds/<image>
mise run check --confirm         # this commit's check receipt, with its image
uv run xyf_build.py --xyf-product --out ../private/xy-forever/builds/repeat   # by hand: same candidate SHA
uv run xyf_build.py --verify --out ../private/xy-forever/builds/repeat        # {"passed": true}
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
mise run test                    # the fast tier (about 2 s): any time
mise run test-changed            # after each change: the tests it affects, any tier (testmon)
mise run test-one TARGET         # one file, class or test (tests/test_aes.py::NativeAesTests)
mise run test-failed             # what failed last time
mise run check                   # once per PR: the gate, under 30 s
mise run test-all                # every tier but the parked sweeps, the simulator core included; CI runs it
```

The suite runs under pytest (config: `pyproject.toml`; tiers, budgets and plugins: `tests/conftest.py`). Each
test is in a tier: `fast` (the default), `gate` (slower: sanitizer builds, fuzzing, the published kit), `sim`
(the simulator core: about 50 tests, each saying in its docstring why it is kept) or `sweep` (power-cut and fault
sweeps on the simulator, parked until they move to native tests: `mise run test-sweeps`, in no routine run).
Budgets are enforced: the fast tier 3 s and 0.3 s a test, the gate tier 15 s and 10 s a test, the sim tier 5 s
and 1.5 s a test; going over fails the run and names the test or tier (make it faster or move it
a tier). Every test is bounded (`timeout`, `bounds.TEST_S`; a simulator core test `SIM_TEST_S`, a parked sweep `SLOW_S`): past it the run
prints HUNG with every thread's traceback and its worker's process group ends. `test-changed` adds to testmon's
Python tracking the C++ each test compiled (`hostbuild`'s `clang -M` record) and the files a test runs as a
subprocess (`@pytest.mark.uses('scripts/x.py')`); its first run runs everything.

`mise run check` (`scripts/check.py`) is the PR gate on a clean, committed tree: the product build into
`build/product` plus `--verify` (cached, below; a verified build is copied to `../private/xy-forever/builds/<image>`), `protogen --check`, `pnpm run typecheck` in
`clients/ts` and `web` when they differ from the merge base with `origin/main` (or with `--js`; it stops first,
naming `pnpm install`, when a needed `node_modules` is missing), then the fast and gate tiers with a temporary
copy of the verified build. Budgets: 15 s a step, 30 s in all. Step output goes to a log in
`../private/xy-forever/sessions/<date>-check/`. A passing run writes the receipt
`../private/xy-forever/checks/<tree>.json` (`git rev-parse HEAD^{tree}`): a cache, so a rerun on the same tree
stops at once (`--force` reruns); `--confirm [REF]` prints what it records. `mise run neutral [REF]`
(opt-in) compares every build mode with REF and reuses the check's product build. CI (`.github/workflows/
checks.yml`: every tier without private files, with open-xy's public toolchain; the web and client suites) is
the merge check. Neither stands in for a device test. Firmware may also be built into the checkout's gitignored
`build/` folder; TE bytes are never committed or uploaded.

`mise run build`, `mise run verify` and the check's build steps go through Turborepo's local cache
(`scripts/firmware_cache.py`, `turbo.json`). The key is every file `xyf_build.py` hashes as its sources, plus the
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
