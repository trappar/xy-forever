# Operating the device: first device install

Part of [Operating the device](../OPERATING.md). After [setup without a device](../../ONBOARDING.md). Read
[SAFETY.md](../SAFETY.md) first: it binds every step here. Give the developer hands-on steps one short request
at a time (at most three simple steps each), and record the results in your status notes
(`<private>/xy-forever/status/README.md`).

## What the developer needs

- An OP-XY still on stock 1.2.1, never updated to 1.2.5: a 1.2.5 device cannot run XY Forever, and
  teenage engineering's built-in updater cannot downgrade it. To tell: the OS shows 1.2.1, and te-boot (hold
  COM while switching on) shows "3. dev menu" (1.2.5 has none; on 1.2.1, Open-XY's browser builder can enable
  it: open-xy `docs/builder.md`). The stock files below confirm it.
- A USB cable, and an MTP client as a fallback: OpenMTP on macOS (quit Field Kit first), File Explorer on
  Windows, the file manager on Linux.

## 1. Stock files from the developer's OP-XY

XY Forever builds on the stock 1.2.1 `os.ldr` and `bootloader.ldr`. They are not distributed; they come off
the developer's own device, read only.

1. Ask: switch the OP-XY off; hold **COM** while switching it on; press **Track 3** ("3. dev menu"), then
   **Track 2** ("2. mtp file transfer (mac)"; Track 3 is the Windows and Linux mode).
2. Run `uv run scripts/setup.py import-stock --from-device`. It reads the pair from
   `/upgrade/firmware/blackfin/bin/` and `/content/op1_factory.json` (teenage engineering's catalog) into
   `private/incoming/`, then checks the pins (`stock.json`).
3. Ask: press **Track 2** ("2. exit"), then switch the OP-XY off.

If step 2 fails, its one line says why. Fallback: the developer copies those files with the MTP client into
`private/incoming/`; then run `uv run scripts/setup.py import-stock ../private/incoming`.

- **1.2.5 files: stop.** This device is on 1.2.5 and cannot run XY Forever: the 1.2.5 bootloader has no dev
  menu, and the built-in updater cannot downgrade it (SAFETY.md, "Never").
- Official `.tfw` packages and update keys are never distributed or made from the stock pair: their tests skip.

Then `mise run setup` again: it runs `setup.py fixtures` (network: public open-xy and its kit; one verified
product build). `mise run doctor` shows the stock and fixture rows ok.

## 2. Approvals for this device

Device work needs the developer's explicit yes (SAFETY.md, [ORCHESTRATION.md](../ORCHESTRATION.md)
"Approvals"). Two kinds can be given once, for their own device; ask both in one question, recommending yes,
and record the answer and its scope in the status notes ("Standing approvals"), never in git:

1. **Host access:** agents may operate this OP-XY through XY Forever's own USB service (`xyf_host.py` and the
   scripts built on it), under SAFETY.md. Without it, every device item waits for a yes.
2. **Routine USB installs:** a build that passed `--verify` and the full suite, carrying one understood change,
   is installed without a separate yes; what is being installed is still said first. New or risky behaviour
   (storage writes, new every-boot paths, new reset behaviour) is asked each time.

Never standing: anything SAFETY.md lists under "Never", companion chips, 1.2.5, relaxing a builder audit, a new
kind of storage write. Each is asked for that exact operation.

## 3. First install and recovery kit

First the dev-menu kit that undoes the install (SAFETY.md, recovery ladder step 2):

```sh
mkdir -p ../private/xy-forever/recovery/stock-1.2.1
cp ../private/firmware/stock-1.2.1/os.ldr ../private/xy-forever/recovery/stock-1.2.1/os.ldr
```

The first install goes through the dev menu (no XY Forever service yet). Install the `candidate.ldr` of the
build `fixtures` verified (`doctor`, "verified build"), as in [at the device](at-the-device.md#dev-menu):

1. Ask: COM at power-on, Track 3, Track 2 (MTP).
2. Ask: copy the candidate to the device as `/os.ldr` with the MTP client (not 0 bytes).
3. Ask: Track 2 (exit), Track 5 ("write os to boot partition"), wait for "firmware written ok!", switch off
   and on.

At first start XY Forever checks the device's stock copy in `/upgrade`, adds OS 1.2.1 to the library and makes
it the default; let the "Adding OS 1.2.1" progress finish. No key is needed for that first start. To open the
menu later, start `uv run xyf-host --timeout 120 status` first, then ask for a power-on holding PLAY, released
at the logo. First-start errors: the [guide](../GUIDE.md).

## 4. Device pass and known-good kit

Run `uv run scripts/device_pass.py --wait 120` (`--plan` first shows every step), relaying its `NEED:` line
([device pass](device-pass.md)). When it passes, copy the build's `candidate.ldr` to
`../private/xy-forever/recovery/known-good-<image>/os.ldr` and fill in the status notes: device state,
installed build, known-good build and kit. End in keepalive, never in the OS.
