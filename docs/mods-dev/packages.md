# The mod package tools (XY Forever contributors)

Part of [Writing mods for XY Forever](../MODS-DEV.md). These need an XY Forever checkout; a mod writer uses `xyf`
instead. Your computer never builds firmware for the device: it turns an open-xy kit into packages, one per machine or
extension, and copies them to the OP-XY, which links the chosen packages into the stock 1.2.1 firmware itself,
installs the matching content, and boots the result.

## 1. Make packages from a kit

`xy_forever/modkit.py` reads a published open-xy kit (format `opxy-browser-kit/1`)
and writes one package set:

```sh
uv run -m xy_forever.modkit split KIT.json --sdk OPEN_XY --out DIR \
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
recognises). Run `uv run -m xy_forever.modkit --help` for their options.

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
- A new package holds `manifest.json` and `files.sha256`. The device checks
  listed file hashes and also accepts legacy `info.txt` metadata.
- A machine package holds its objects, `objects.txt`, `catalog.txt`, and its content
  (`content/display/…`, `content/presets/<id>/…`).

The full format is in [storage/mods.md](../storage/mods.md), section "Mod packages".

## 2. Send them to the device

```sh
uv run xyf-host mods push DIR/mods/opxy-1.2.1/<generation> [--only ID ...] [--verify]
```

The set goes to `/yaffs2/xy-forever/mods/<generation>/`, stored flat (protocol 1.16 or later;
[the store](../storage/mods-store.md)). `mods list` names an older layout, too deep for the dev menu's MTP:
`mods remove <folder> --yes`, then push again. The base is always sent. A file in a folder is stored under
its SHA-256. A changed package is built and SHA-256 verified in `tmp/`, then renamed into its place;
its old folder is detached first. A package with the same files and `files.sha256` is skipped.
If a push stops part-way, run it again: an unpublished scratch
folder is swept, and no half-sent package becomes visible. See [package transactions](../storage/package-transactions.md).

`mods list` shows what is on the device; `mods remove GENERATION --yes` deletes a
set (without `--yes` it is a dry run).

## 3. Build and boot today

The library needs the stock 1.2.1 firmware as an entry, with its `content.sha256`.
Without it booting from the first row stops with "Failed: no stock OP-XY firmware in the library."
The first start adds it; the first build writes its content set from the live content.
`uv run xyf-host library-add ... --content DIR` adds both (DIR: the official package's
`firmware/blackfin/content`).

**On the device (the normal way):** power on holding PLAY, open Mods, turn your
machines on (saved at once), STOP, then PLAY on the first row. The OP-XY builds,
installs content, sets the default and boots it.

**From the computer:** `uv run xyf-host mods set MACHINE ... [--ext ID ...]` saves the same
selection (checked, or refused with the reason; none: all off); it is built at the next start.

## 4. Try a mod you are writing

`uv run xyf-host mods dev DIR/mods/opxy-1.2.1/<generation>/<mod>` builds that mod alone on the OP-XY and boots it
once; nothing saved changes. [How it works](try.md).
