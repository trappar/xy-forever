# Operating the device: at the device

Part of [Operating the device](../OPERATING.md).

The rules in [SAFETY.md](../SAFETY.md) are binding for everything here.

## Taking control of the device (no dev menu needed)

1. Start a listener first: `uv run xyf-host --timeout 3600 status` (or a session
   script). Only then ask the developer to **power on holding PLAY and let go when the
   logo appears** (holding it longer is fine).
2. The PLAY boot opens the boot menu (home) with USB; it answers the tool's HELLO and,
   once a host said HELLO, never times out. `hold on` keeps home across restarts
   (every non-OS restart from home comes back home anyway).
3. Keep it held: `scripts/keepalive.sh STATEDIR` (a status call every ~2 s; stop
   with `touch STATEDIR/stop-keepalive`). Use a folder under
   `../private/xy-forever/sessions/` as STATEDIR.
4. Booting the real OS is one-way: only a power cycle at the device returns. Test boot
   paths with `continue --dry-run`, and don't leave the device in the OS.
5. A misbehaving build: see the recovery ladder in [SAFETY.md](../SAFETY.md).

## Timing

- Boots are not stopwatch-timed by hand. Estimate from device data:
  the dry-run result's `start_cycles` (B's entry since reset, ×1024/400 MHz),
  `link_ms`, the timing block and the transfer detail (`dry_run_file_boot.detail`),
  interpreted with [hardware/boot.md](../hardware/boot.md). After a dry run,
  `status` shows that boot's phases in `dry_run_file_boot.timing` (`boot_timing` is
  the current boot). `null` means not measured; A and B have different time origins,
  and `end_ms` includes time spent in the menu.
- `scripts/usb_timeline.py` logs when OP-XY USB devices appear and disappear
  (read-only), e.g. to confirm a real boot reached the OS.

## Reports

- **Report files:** each processor writes one report after the OS mounts storage.
  Files are `/yaffs2/xy-forever/log/prod-{a,b}.bin` (`pfile-` from a library image); the
  next boot overwrites them. Inside, B's USB service block names the release version and image
  (as HELLO `version=` and `image=`) that wrote them; builds before #107 wrote `prod-NNN-{a,b}.bin`.
- **Fetch:** `uv run xyf-host read PATH --all --out FILE`, or dev MTP (below).
- **Decode:** `uv run -m xy_forever.census FILE.bin`.
- **Dry-run summary:** `uv run scripts/dryshow.py STATUS.json`.
- **Evidence capture:** `scripts/capture_session.py --out DIR --state --report prod`
  prints a plan; add `--capture` to run it (HELLO, STATUS and the chosen reads only;
  HELLO marks a host as present, so not for host-free menu tests).
- **Storage:** keep fetched reports in `../private/xy-forever/reports/<test>/`.

## Dev menu

1. Stop every host tool, including keepalive, before the developer enters the stock dev menu.
   The developer holds **COM** at power-on.
2. **MTP:** Track 3 opens the dev menu, then Track 2 starts Mac MTP. Use OpenMTP on
   the Mac.
3. **Copy:** copy `os.ldr` to `/os.ldr`. Check the copied file is not 0 bytes, then
   exit MTP.
4. **Write:** Track 5 writes the OS to the boot partition. Wait for "firmware
   written ok!", then power-cycle.

Copying `/os.ldr` alone installs nothing; the Track 5 step does. For a stock OP-XY the web app's Install page
(#178, `web/README.md`) leads an owner through these steps, switching the dev menu on over Web MIDI first; the first
power-on after Track 5 then adopts the stock 1.2.1 entry on the device (`docs/storage/jobs.md`). Reports can be
fetched through dev MTP without the USB service. To do that, create the destination
folder on the Mac first and copy the report folder (see Reports). `/logs` survives
an OS reinstall, so crash logs can be fetched from it.

## Working with the developer at the device

- Give brief, simple hardware instructions: at most three simple steps per request, and ask first
  whether they have time. Ask only whether it booted or what the screen shows, and read the reports
  yourself. Don't ask them to read files back by hand, and never ask them to repeat a crash.
- After stopping keepalive, wait 2 s before the next host operation.
- **Power cycles:** start the listener first, then just say "power cycle it" (no pause needed). To
  take control, they power on holding PLAY and release at the logo; PLAY is only for reaching the menu.
- **Ambiguous requirements:** restate them as a situation → behaviour table and
  confirm before building.
- **Hardware time is limited.** Pack more information into each test, plan boots
  carefully, and leave no junk on the device.
- **Uninstall needs no stock bootloader or dev menu.** It is the web app's (#179,
  the USB `boot.uninstall` command), not a home menu row (#197).
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
- **Browser tests run by automation.** On the OP-XY: `node scripts/device-browser-test.ts --out DIR
  --serial` in `web` drives the web app in the system Chrome (Playwright), no human step;
  `mise run web-e2e` covers the simulator. Chrome's serial port chooser
  cannot be clicked by automation: a Chrome profile holding a grant gets past it, made once by hand with
  `--grant` (`web/docs/testing.md`). Never ask the developer to click through a page; they are needed
  only for physical actions (power, keys, the OP-XY's own screen).
- **Cleanups are logged.** Removing files from the device or the private folder writes
  `../private/archive/cleanup-<date>-<topic>.json` first: what, where from, why, and how to
  restore it.
- **Keys** are read from `../private/keys/opxy-update-keys.json`. Never print or
  commit them.
