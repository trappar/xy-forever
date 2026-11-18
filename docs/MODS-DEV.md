# Writing mods for XY Forever

For people who write open-xy mods (machines and extensions) and want to try them on
an OP-XY that runs XY Forever. This describes the current version (serial protocol
1.13). The user side of the Mods page is in [GUIDE.md](GUIDE.md).

## How it works

Your computer never builds firmware for the device. It turns an open-xy kit into
packages, one per machine or extension, and copies them to the OP-XY. The OP-XY
links the chosen packages into the stock 1.2.1 firmware itself, installs the matching
content, and boots the result.

## 1. Make packages from a kit

`xy_forever/modkit.py` reads a published open-xy kit (format `opxy-browser-kit/1`)
and writes one package set:

```sh
python3 -m xy_forever.modkit split KIT.json --sdk OPEN_XY --out DIR \
    [--sdk-commit C] [--sdk-source URL@COMMIT] [--extension-catalog extensions.json]
```

- `--sdk` is an open-xy tree at the kit's SDK commit. `split` runs open-xy's own
  `builder/core.mjs` under Node to finish the content (display art, presets). It never
  changes that tree. With `--sdk-commit`, it takes that commit from the checkout.
- Objects are copied unchanged. No device and no network are used.
- How to get a kit file from open-xy is not covered here (not verified).

Other subcommands are for checking the device's assembly against open-xy:
`rebuild` (packages back into a kit), `prove` and `prove-patch` (byte-for-byte
comparisons with `core.mjs`), and `known-base` (the list of stock bases the device
recognises). Run `python3 -m xy_forever.modkit --help` for their options.

## What a package set looks like

`split` writes `DIR/mods/<profile>/<generation>/`:

```
<generation>/                 for example 0.0.6-<12 hex>
  base/                       the mod base: SDK objects, edits, tables, the memory limit
  <machine id>/               one folder per machine
  ext-<extension id>/         one folder per extension
```

- `<profile>` is the OS the set targets, today `opxy-1.2.1`.
- `<generation>` is the kit version plus 12 hex digits of a hash over the whole set.
  Packages from different generations never mix.
- Every package folder holds an `info.txt` (`key=value`) and a `files.sha256` that
  lists every other file in it. The device checks each file it reads against that
  list.
- A machine package holds its objects, `objects.txt`, `catalog.txt`, and its content
  (`content/display/…`, `content/presets/<id>/…`).

The full format is in [storage/mods.md](storage/mods.md), section "Mod packages".

## 2. Send them to the device

```sh
python3 xyf_host.py mods push DIR/mods/opxy-1.2.1/<generation> [--only ID ...] [--verify]
```

The set goes to `/yaffs2/xy-forever/mods/<generation>/`, stored flat (protocol 1.16 or later;
[the store](storage/mods-store.md)). `mods list` names an older layout, too deep for the dev menu's MTP:
`mods remove <folder> --yes`, then push again. The base is always sent. A file in a folder is stored under
its SHA-256, so an edit always sends it; a top-level file (`catalog.txt`, ...) with the same size is
skipped: after such an edit, `mods remove ID --yes` first, or use `mods dev` (it compares SHA-256). `--verify`
reads every file back and stops on a difference. If a push stops part-way, run it
again. `files.sha256` is written last, so a half-sent package is
never used.

`mods list` shows what is on the device; `mods remove GENERATION --yes` deletes a
set (without `--yes` it is a dry run).

## 3. Build and boot today

The library needs the stock 1.2.1 firmware as an entry, with its `content.sha256`.
Without it Save stops with "Save failed: no stock OP-XY firmware in the library."

**On the device (the normal way):** power on holding PLAY, open Mods, turn your
machines on, STOP, then PLAY on the first row and choose Save. The OP-XY builds,
installs content, saves the setup and boots it.

**From the computer:**

```sh
python3 xyf_host.py mods apply --base ID --generation opxy-1.2.1/<generation> MACHINE ... \
    [--ext ID ...] [--name NAME] [--wait]
```

This builds into library entry `mods-a` or `mods-b` and stops there. Then make that
entry the default (SHIFT + PLAY in the Firmware library, or `set-default ID
--yes-write`), which installs its content first. `mods cancel` stops a running build.

## Limits

- **Memory:** the mods share one region of fixed size, given by the base package
  (`module_bytes`). The Mods page shows an estimate from package sizes. Over the
  limit, it refuses to build ("Too big for the mods memory").
- **At most 64 machines and 8 extensions in one build.** At least one machine.
- **At most 96 packages** in a set are read by the Mods page.
- **The base must be a stock image XY Forever knows.** An unknown base is refused
  unless the user allows it once (SHIFT + PLAY on the Mods page's first row) or you
  pass `--unverified` to `mods apply`.
- A mod may not replace official content files.

## Not yet

- No one-command edit, build and boot loop. Today it is `split`, `mods push`, then a
  build on the device and a boot by hand.
- No measuring build: the memory figure is an estimate, not the size after linking.
- No CPU or stability checks of a mod before it boots. A mod that crashes the OS needs
  a power-on holding PLAY to get back to the menu (not verified for every crash).
- Only the stock 1.2.1 base. Choosing another base firmware is planned.
