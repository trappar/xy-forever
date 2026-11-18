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
python3 -m unittest discover -s tests
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

The device-pass script currently enables the broken guard in its unconditional session stage.
Do not run it on hardware, even with selected stages, until [issue #1](https://github.com/trappar/xy-forever/issues/1)
removes that behavior. `--plan` and simulator runs remain available offline. Every hardware qualification
still needs the checks and approvals in [SAFETY.md](../SAFETY.md).
