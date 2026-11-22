# Operating the device: build, verify, install

Part of [Operating the device](../OPERATING.md).

The rules in [SAFETY.md](../SAFETY.md) are binding for everything here.

## Build, verify, install (every build)

```sh
cd xy-forever        # toolchain: ../tools/blackfin (elsewhere: docs/operating/dev-tools.md)
mise run doctor                  # stock pair + fixtures in place? (first: operating/first-install.md)
mise run check                   # the PR gate (checks.md): the verified build lands in builds/<image>
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
offline.

What protects an install: the build runs every CRC and call audit, `--verify` repeats the build and
compares it; the USB install writes through the stock commit-last A/B writer (`mmc_tools` `88a06a7e`,
Track 5) and reads it back; and COM at power-on keeps the dev-menu recovery ([SAFETY.md](../SAFETY.md),
recovery ladder).

## Checks

Tests, tiers, budgets, the PR gate, the firmware cache: [checks](checks.md).
