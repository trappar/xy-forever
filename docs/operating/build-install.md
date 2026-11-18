# Operating the device: build, verify, install

Part of [Operating the device](../OPERATING.md).

The rules in [SAFETY.md](../SAFETY.md) are binding for everything here.

## Build, verify, install (every build)

```sh
cd xy-forever        # toolchain: ../tools/blackfin (or set XYF_TOOLCHAIN)
python3 scripts/setup.py doctor   # stock pair + fixtures in place? (first: bootstrap, import-stock DIR)
python3 xyf_build.py --xyf-product --out ../private/xy-forever/builds/xy-forever-product-current
python3 xyf_build.py --xyf-product --out ../private/xy-forever/builds/xy-forever-product-repeat  # same candidate SHA
python3 xyf_build.py --verify --out ../private/xy-forever/builds/xy-forever-product-current      # {"passed": true}
python3 scripts/check.py --confirm   # this commit's check receipt (none: python3 scripts/check.py)
python3 scripts/device_pass.py --plan --install ../private/xy-forever/builds/xy-forever-product-current  # inspect the qualification plan
```

`--xyf-product` is the product (boot model v3) and the only installable mode (the
v2 two-session flow was removed). Local `docs/status/` names the installed and known-good builds. Builds are deterministic, but `--verify` from a later checkout
fails with "Source changed since build": re-verify an old build from its own commit.
Only the install's verify reply proves an install; a later successful boot does not.

For each new build, bump the build number in `xyf_build.py` (the mode's entry in `MODES`
and `FILE_VARIANT_REPORTS`) so the report names show which build ran
(`prod-<build>-{a,b}.bin`; the number identifies private evidence). Delete the repeat build
once its SHA matches. Prepare a dev-menu kit (copy `candidate.ldr` to
`../private/xy-forever/recovery/known-good-current/os.ldr`) once a build has proven
itself on the device.

A change that should not alter the firmware (refactor, comments, dead code): run
`python3 scripts/neutral.py [REF]` instead. It builds every mode from `REF`
(default `HEAD`) and from the working tree and compares the candidates. Its
builds go to `../private/xy-forever/sessions/neutral/`.

The device pass turns on no watchdog: there is none (protocol 1.14). `--plan` and simulator runs work
offline. Every hardware qualification still needs the checks and approvals in [SAFETY.md](../SAFETY.md).

## Checks and receipts

```sh
python3 scripts/run_tests.py test_a test_b.SomeTests   # while iterating: the tests you touch (--times: slowest)
python3 scripts/check.py                               # the end of every change, on its commit: a receipt
python3 scripts/check.py --neutral                     # also scripts/neutral.py against main's merge base
python3 scripts/check.py --confirm BRANCH              # seconds, no build: what the branch's receipt records
```

`scripts/check.py` runs once on a clean, committed tree: the exhaustive suite (`XYF_EXHAUSTIVE=1
scripts/run_tests.py`, every test, never a subset), the product build into
`../private/xy-forever/builds/check-<tree>` plus `--verify`, `scripts/neutral.py` with `--neutral [REF]`, and
`npm test` in `clients/ts` when that folder differs from the merge base with `origin/main` (else local `main`)
or with `--npm`; it stops first, naming `npm ci`, when `clients/ts/node_modules` is missing. Step output goes
to a log in `../private/xy-forever/sessions/<date>-check/`; it prints a short summary. Only when every step
passed and the tree was clean (`git status --porcelain` empty) at the start and the end does it write the
receipt `../private/xy-forever/checks/<tree>.json`, keyed to `git rev-parse HEAD^{tree}`: each step's command,
result and time, the test counts, the candidate SHA-256 and size, the build folder. A failed run leaves
`<tree>.failed.json` instead and removes that tree's receipt.

`--confirm [REF]` (default `HEAD`) exits 0 only for a passing receipt of REF's tree, and prints it; otherwise
it says what is missing: no receipt, a failed check, or a receipt for an older tree of the branch (rerun after
a rebase or a merge onto a newer main). The orchestrator and merging use the worker's receipt instead of
rerunning. Host bridges and the simulator are cached per input hash in `../private/xy-forever/sessions/`, so a
run after a source change is slower. A receipt never stands in for a device test.
