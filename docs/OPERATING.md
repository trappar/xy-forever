# Operating the device

How to build, install and test XY Forever on the developer's OP-XY, and how to work
with the developer at the device. The rules in [SAFETY.md](SAFETY.md) are binding for everything here.

## Build, verify, install (every build)

```sh
cd xy-forever        # toolchain: ../tools/blackfin (or set XYF_TOOLCHAIN)
python3 scripts/setup.py doctor   # stock pair + fixtures in place? (first: bootstrap, import-stock DIR)
python3 xyf_build.py --xyf-product --out ../private/xy-forever/builds/xy-forever-product-0NN-001
python3 xyf_build.py --xyf-product --out ../private/xy-forever/builds/xy-forever-product-0NN-002  # same candidate SHA
python3 xyf_build.py --verify --out ../private/xy-forever/builds/xy-forever-product-0NN-001      # {"passed": true}
python3 -m unittest discover -s tests
python3 scripts/device_pass.py --install ../private/xy-forever/builds/xy-forever-product-0NN-001  # install + prove
```

`--xyf-product` is the product (boot model v3) and the only installable mode (the
v2 two-session flow was removed). STATUS names the installed and
known-good builds. Builds are deterministic, but `--verify` from a later checkout
fails with "Source changed since build": re-verify an old build from its own commit.
Only the install's verify reply proves an install; a later successful boot does not.

For each new build, bump the build number in `xyf_build.py` (the mode's entry in `MODES`
and `FILE_VARIANT_REPORTS`) so the report names show which build ran
(`prod-0NN-a.bin` / `-b.bin`, file image `pfile-0NN-#.bin`). Delete the `-002` copy
once its SHA matches. Prepare a dev-menu kit (copy `candidate.ldr` to
`../private/xy-forever/recovery/known-good-0NN/os.ldr`) once a build has proven
itself on the device.

A change that should not alter the firmware (refactor, comments, dead code): run
`python3 scripts/neutral.py [REF]` instead. It builds every mode from `REF`
(default `HEAD`) and from the working tree and compares the candidates. Its
builds go to `../private/xy-forever/sessions/neutral/`.

## Taking control of the device (no dev menu needed)

1. Start a listener first: `python3 xyf_host.py --timeout 3600 status` (or a session
   script). Only then ask the developer to **power on holding PLAY and let go when the
   logo appears** (holding it longer is fine). Up to build 064 this sometimes
   failed (HISTORY, Diag 076: a slow storage mount, not the key) and the device booted
   the OS after about 15 s.
2. The PLAY boot opens the boot menu (home) with USB; it answers the tool's HELLO and,
   once a host said HELLO, never times out. `hold on` keeps home across restarts
   (every non-OS restart from home comes back home anyway).
3. Keep it held: `scripts/keepalive.sh STATEDIR` (a status call every ~2 s; stop
   with `touch STATEDIR/stop-keepalive`). Use a folder under
   `../private/xy-forever/sessions/` as STATEDIR.
4. Booting the real OS is one-way: only a power cycle at the device returns. Test boot
   paths with `continue --dry-run`, and don't leave the device in the OS.
5. A misbehaving build: see the recovery ladder in [SAFETY.md](SAFETY.md).

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

## Host tool `xyf_host.py` (stdlib; macOS `/dev/cu.usbmodem*`)

`wait`, `status`, `read PATH [--all --out F]`, `install BUILD --yes-install`,
`reboot --yes-reboot [--to service|os|menu]`, `set-default ID --yes-write`,
`put-file LOCAL /yaffs2/xy-forever/PATH --yes-write` (creates missing folders),
`library-add ID OS_LDR --name --version --yes-write`, `screenshot --out F.png`,
`continue [--dry-run]`, `boot ID --yes-boot` (1.7: boot a library entry once; leaves XY Forever), `key K`, `hold on|off`, `guard on|off|expiry-test` (processor B only since 164),
`test-hang`,
`uninstall STOCK_OS_LDR --yes-uninstall`.

Every write or install is a dry run without its `--yes-*` flag. Writes are limited
to `/yaffs2/xy-forever/` and (build 168+) `/yaffs2/content/`. Example: creating the report folder before installing
build 067+ (a storage write, so ask the developer first):
`python3 xyf_host.py put-file some.txt /yaffs2/xy-forever/log/README.txt --yes-write`.

### File commands (build 165+, protocol 1.5)

Absolute device paths under `/yaffs2`; the device refuses `/yaffs2/bootloader.dev`, `/yaffs2`
itself and anything outside `/yaffs2`. Build 168+: `put`, `rm` and `mkdir` only under
`/yaffs2/xy-forever/` and `/yaffs2/content/` (no name holding `bootloader`). Changes go through the device's write window (closed
8 s after the last change). `rm`, `mkdir`, `put`, `mv` and `cp` act at once (no `--yes-*` flag). Build 169+:
`mv` renames or moves a file or a whole folder within those two folders; the target must not exist.
Build 170+: `cp` copies on the device (the bytes never cross USB) from any readable file to a new name
in those folders; `cp -r` copies a folder (the tool lists it and copies file by file).

```sh
python3 xyf_host.py ls /yaffs2/xy-forever [--json]       # type, size, name (paged; any folder size)
python3 xyf_host.py get /yaffs2/xy-forever/log/x.bin x.bin   # any size; size (ls) and 4 ranges re-read
python3 xyf_host.py put x.bin /yaffs2/xy-forever/x.bin   # <= HELLO stage= (7 MiB); read back [--no-verify]
python3 xyf_host.py mkdir /yaffs2/xy-forever/new         # the parent must exist
python3 xyf_host.py rm /yaffs2/xy-forever/x.bin          # rm --dir: an empty folder only
python3 xyf_host.py mv /yaffs2/xy-forever/a /yaffs2/content/b   # file or folder; no overwrite (state)
python3 xyf_host.py cp [-r] /yaffs2/xy-forever/a /yaffs2/content/b   # <= 7 MiB per file; no overwrite (state)
python3 xyf_host.py mods apply --base opxy-121 --generation opxy-1.2.1/GEN fm2op [--ext ID...] --wait   # B assembles into tmp/job-N/
python3 xyf_host.py mods cancel                          # stop it (removes what it wrote)
```

## Timing

- Boots are not stopwatch-timed by hand. Estimate from device data:
  the dry-run result's `start_cycles` (B's entry since reset, ×1024/400 MHz),
  `link_ms`, the timing block and the transfer detail (`dry_run_file_boot.detail`),
  calibrated against the measured points in HARDWARE.md. After a dry run,
  `status` shows that boot's phases in `dry_run_file_boot.timing` (`boot_timing` is
  the current boot). `null` means not measured; A and B have different time origins,
  and `end_ms` includes time spent in the menu.
- `scripts/usb_timeline.py` logs when OP-XY USB devices appear and disappear
  (read-only), e.g. to confirm a real boot reached the OS.

## Reports

- **Report files:** each processor writes one report after the OS mounts storage.
  Build 067 and later write `/yaffs2/xy-forever/log/prod-0NN-{a,b}.bin`; builds up
  to 066 wrote `/yaffs2/chained-loader/`.
- **Fetch:** `xyf_host.py read PATH --all --out FILE`, or dev MTP (below).
- **Decode:** `python3 -m xy_forever.census FILE.bin`.
- **Dry-run summary:** `python3 scripts/dryshow.py STATUS.json`.
- **Evidence capture:** `scripts/capture_session.py --out DIR --state --report NNN`
  prints a plan; add `--capture` to run it (HELLO, STATUS and the chosen reads only;
  HELLO marks a host as present, so not for host-free menu tests).
- **Storage:** keep fetched reports in `../private/xy-forever/reports/<test>/`.

## Dev menu

1. The developer holds **COM** at power-on.
2. **MTP:** Track 3 opens the dev menu, then Track 2 starts Mac MTP. Use OpenMTP on
   the Mac.
3. **Copy:** copy `os.ldr` to `/os.ldr`. Check the copied file is not 0 bytes, then
   exit MTP.
4. **Write:** Track 5 writes the OS to the boot partition. Wait for "firmware
   written ok!", then power-cycle.

Copying `/os.ldr` alone installs nothing; the Track 5 step does. Reports can be
fetched through dev MTP without the USB service. To do that, create the destination
folder on the Mac first and copy the report folder (see Reports). `/logs` survives
an OS reinstall, so crash logs can be fetched from it.

## Working with the developer at the device

- Give brief, simple hardware instructions. Ask only whether it booted or what
  the screen shows, and read the reports yourself. Don't ask them to read files back
  by hand, and never ask them to repeat a crash.
- **Power cycles:** start the listener first, then just say "power cycle it". To
  take control, they power on holding PLAY and release at the logo. Ask for a pause
  of about 5 s on power cycles.
- **Ambiguous requirements:** restate them as a situation → behaviour table and
  confirm before building.
- **Hardware time is limited.** Pack more information into each test, plan boots
  carefully, and leave no junk on the device.
- **Uninstall must be a real in-menu action.** Users must never need the stock
  bootloader or dev menu.
- **The developer boots test firmware.** When a test needs a library entry booted (the device
  leaves XY Forever), prepare everything over USB, then name the entry exactly as the menu shows
  it and what to look for; the developer boots it, checks it and power cycles back, and reports in
  their own words. Uploads, checks and cleanup stay automated.
- **Commands, not key presses.** Anything a script needs the device to do goes through a protocol
  command; injected keys (`key K`) are only for tests of the user interface itself. If a command
  is missing, add it rather than script the menu.
- **Waits sized to the outcome.** Every wait in a device script is the time the result is known
  plus a few seconds (a probe answers within 3 s; a freeze shows within seconds), polled rather
  than one long wait. Report the moment the result is clear ("frozen: power cycle it"); never
  leave the developer waiting out a long timeout.
- **Browser tests run by automation** (Playwright on the system Chrome: `npm run test:browser`
  in `clients/ts`), against the real device too. Never ask the developer to click through a page.
  Chrome's serial port chooser is the one step page automation cannot click: verify a way past
  it (DevTools device-prompt handling, or a managed policy granting the device) before relying
  on it. The developer is needed only for physical actions (power, keys, the OP-XY's own screen).
- **Cleanups are logged.** Removing files from the device or the private folder writes
  `../private/archive/cleanup-<date>-<topic>.json` first: what, where from, why, and how to
  restore it.
- **Keys** are read from `../private/keys/opxy-update-keys.json`. Never print or
  commit them.
