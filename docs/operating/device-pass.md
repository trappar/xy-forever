# Operating the device: device pass and mod boot

Part of [Operating the device](../OPERATING.md).

The rules in [SAFETY.md](../SAFETY.md) are binding for everything here.

## Device pass (`scripts/device_pass.py`)

The regression that proves a build on the device; run by the orchestrator only
(ORCHESTRATION.md, "The device"). `--plan` prints every step and its wait and sends
nothing. Default stages: read, protocol, boot (dry run, restart, keys), writes, mtp;
`--install BUILD_DIR` adds the install, `--guard-test` the guard stage (last, never by default:
one guard expiry-test after a read while A runs; B resets, A follows ~10.4 s later, home within
20 s; a miss needs one power cycle). `--stages` picks stages.

- **Protocol** (after read and install, before any restart): records the HELLO; on a
  pre-v1 device (build 153 and earlier) every step is SKIP "pre-v1 device". On v1:
  after `--install`, `build=` and `cmds` must match that build (shows which build
  runs); `unsupported`, `refused`, `bad_request`, CONTINUE mode 2 `link`, with requests
  that change nothing (a staging is never complete; `stage.end` discards it); HELLO
  round trip, read and stage KiB/s in the table and `summary.json`. `--big-replies`
  adds replies above HELLO `read=` and 4 KiB `stage.data`: can hang the device.

- **Needs** the device at home. Without an answer it prints one `NEED:` line and
  exits 3 with nothing changed. To start it before asking for the power-on, give
  it `--wait 120`.
- **Guard on** for the whole pass; the first FAIL stops the pass (later steps SKIP),
  but the end steps always run: guard off, hold off, crash count cleared, then
  `scripts/keepalive.sh` on the session folder. It never boots the OS.
- **Evidence:** `../private/xy-forever/sessions/<date>-device-pass-<n>/` (one file
  per step, `summary.json`); it prints a PASS/FAIL/SKIP table and the folder.
  Exit 0 only if nothing failed.
- **Next command:** `touch FOLDER/stop-keepalive` first (the pass stops running
  keepalives itself). It leaves `/yaffs2/xy-forever/log/device-pass.txt` (2,500 B,
  overwritten each pass) and `device-pass-2.txt`; since 165 it removes its library scratch
  entry and its file-command folder `xyf-pass-files` again.

## Mod boot `scripts/mod_boot.py` (`--plan`, `--sim`, NEED/exit 3 like the device pass)

`python3 scripts/mod_boot.py`: assemble fm2op (`--mods a,b`), upload to `firmware/xyf-mods-test/`
(`--replace` if present), read back, menu catalog check. `--content` then saves the device's
`content/op1_factory.json` (`get`, SHA-256) and puts the content delta (`display/<id>.svg`,
`presets/<id>/`, the assembled catalog); refused if the catalog cannot be saved first.
`--boot-once` sends `boot ID` (library.boot: the menu's boot once, no state write); it prints what
to look for and the recovery (power off, 5 s, on holding PLAY). Back in the menu: `--cleanup`
(default unchanged, no `once`; the saved catalog, or `--official-catalog PATH`, put back and read
back by SHA-256; added content files and folders and the entry removed). Evidence:
`sessions/<date>-mod-boot-<n>/`.
