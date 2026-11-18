# Operating the device: device pass

Part of [Operating the device](../OPERATING.md).

The rules in [SAFETY.md](../SAFETY.md) are binding for everything here.

## Device pass (`scripts/device_pass.py`)

The regression that proves a build on the device; run by the orchestrator only
(ORCHESTRATION.md, "The device"). `--plan` prints every step and its wait and sends
nothing. **Hardware execution is blocked:** the script unconditionally enables the broken guard.
Use `--plan` or `--sim` until [issue #1](https://github.com/trappar/xy-forever/issues/1) removes it.
Selecting stages does not skip the guard-on session. Default stages: read, protocol, boot (dry run, restart, keys), writes, mtp;
`--install BUILD_DIR` adds the install. `--stages` picks stages; `--guard-test` is prohibited on
hardware and may be used only in simulator tests.

- **Protocol** (after read and install, before any restart): records the HELLO; on a
  pre-v1 device every step is SKIP "pre-v1 device". On v1:
  after `--install`, `build=` and `cmds` must match that build (shows which build
  runs); `unsupported`, `refused`, `bad_request`, CONTINUE mode 2 `link`, with requests
  that change nothing (a staging is never complete; `stage.end` discards it); HELLO
  round trip, read and stage KiB/s in the table and `summary.json`. `--big-replies`
  adds replies above HELLO `read=` and 4 KiB `stage.data`: can hang the device.

- **Needs** the device at home. Without an answer it prints one `NEED:` line and
  exits 3 with nothing changed. To start it before asking for the power-on, give
  it `--wait 120`.
- **Current script behavior:** guard on for the whole pass (the reason hardware execution is blocked); the first FAIL stops the pass (later steps SKIP),
  but the end steps always run: guard off, hold off, crash count cleared, then
  `scripts/keepalive.sh` on the session folder. It never boots the OS.
- **Evidence:** `../private/xy-forever/sessions/<date>-device-pass-<n>/` (one file
  per step, `summary.json`); it prints a PASS/FAIL/SKIP table and the folder.
  Exit 0 only if nothing failed.
- **Next command:** `touch FOLDER/stop-keepalive` first (the pass stops running
  keepalives itself). It leaves `/yaffs2/xy-forever/log/device-pass.txt` (2,500 B,
  overwritten each pass) and `device-pass-2.txt`; it removes its library scratch
  entry and its file-command folder `xyf-pass-files` again.


Before a future approved full device pass, confirm `content-state.txt` says `official`.
A nonofficial content state needs scoped checks rather than a full write pass.

The old `scripts/mod_boot.py` helper is superseded by native entries and the content switch.
Use `scripts/test_entry.py` to prepare a host-built test entry, subject to SAFETY.md; a library
entry must carry its own content so the device's content switch can install it.
