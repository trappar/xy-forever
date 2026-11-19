# Operating the device: build, verify, install

Part of [Operating the device](../OPERATING.md).

The rules in [SAFETY.md](../SAFETY.md) are binding for everything here.

## Build, verify, install (every build)

```sh
cd xy-forever        # toolchain: ../tools/blackfin (or set XYF_TOOLCHAIN)
python3 scripts/setup.py doctor   # stock pair + fixtures in place? (first: bootstrap, import-stock DIR)
python3 scripts/check.py             # build, --verify, suite: the build lands in builds/<image> (receipt: its commit, tree)
python3 scripts/check.py --confirm   # this commit's check receipt, with its image
python3 xyf_build.py --xyf-product --out ../private/xy-forever/builds/repeat   # by hand: same candidate SHA
python3 xyf_build.py --verify --out ../private/xy-forever/builds/repeat        # {"passed": true}
python3 scripts/device_pass.py --plan --install ../private/xy-forever/builds/IMAGE  # inspect the qualification plan
```

`--xyf-product` is the product (boot model v3) and the only installable mode (the
v2 two-session flow was removed); a device test build (`--xyf-crash-trigger`) installs only for its own test.
Local `docs/status/` names the installed and known-good builds. Builds are deterministic, but `--verify` from a later checkout
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

`scripts/check.py` runs once on a clean, committed tree: the product build into
`../private/xy-forever/builds/check-<tree>` plus `--verify`, the exhaustive suite (`XYF_EXHAUSTIVE=1
scripts/run_tests.py`, every test, never a subset; tests that use a build take only one made from this
checkout's source, `paths.newest_verified_build`), `scripts/neutral.py` with `--neutral [REF]`, and
`npm test` and `npm run typecheck` in `web` when `web` or `clients/ts` differs from the merge base with
`origin/main` (else local `main`), or with `--npm`. `npm test` in `clients/ts` runs once, inside the suite
(`NodeSuiteTest`, whenever its `node_modules` is installed); when `clients/ts` differs (or with `--npm`) the check
requires that `node_modules` so the test cannot skip. It stops first, naming `npm ci`, when a needed
`node_modules` is missing (`setup.py doctor` shows both). Step output goes
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

Tests never open a real device: `run_tests.py`, `check.py` and any process that loads `unittest` set
`XYF_NO_DEVICE` for themselves and every process they start, and with it every real-device opener (the serial
port, USB MTP, pyusb; `xy_forever/no_device.py`) raises "tests may not open a real device" before it looks for
one. There is no opt-out: a test fakes the opener, or uses the simulator (TCP) or a pty. The browser tests run
Chrome without Web Serial.
