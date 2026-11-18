# XY Forever storage layout: mod assembly

Part of the [XY Forever storage layout](../STORAGE.md).

## Assembly after the link

- **Assembly after the link** (`native/mod_patch`, `native/mod_content`, reading the linker's
  result through `native/mod_link.hpp`; host end to end, link included: `python3 -m xy_forever.modkit
  prove-patch`, byte-identical to `core.mjs`). In place in one
  buffer of stock + 16 + `module_bytes` bytes: stock `os.ldr` at 0, the module at `stock_bytes`
  (the linker writes it there); the result is stock with the edits, the module record where FINAL
  was, the module, FINAL. The patcher's facts come from the base's `info.txt` (`mod_patch::read_profile`;
  refusal `base` + the key, or `profile`). The entry's content delta:
  `content/op1_factory.json` (the whole file, never a patch), the packages' `content/display/…`
  and `content/presets|aux_presets/…` unchanged (the job copies them), and `files.sha256` beside
  `content/` listing exactly those `content/…` files, so a content switch removes exactly them.
- **On the device** (`mods.apply`; processor B runs the job): the stock `os.ldr` and the content list are
  the base entry's `firmware/<base>/os.ldr` and `content.sha256`, the stock catalog `/yaffs2/content/op1_factory.json`
  (the job pins its SHA-256; while a mod set's content is installed, the saved `content-official/` copy); output `tmp/job-<id>/`.
  An aux preset path two machines carry is listed once, with the later machine's bytes (as `core.mjs`).
- **The assembled entry** (`native/mod_job`; host reference `modpatch.entry_files`): the job's
  output folder holds every `content/…` file above, `info.txt`, `files.sha256` and `os.ldr`.
  `info.txt`: `type=firmware`, `name` (the caller's), `kind=open-xy`, `bytes`, `crc32`,
  `sha256`, the base's `needs_os`, `generation`, `profile`, and `machines` (the selected
  machine count). Selection and memory choice stay in native `config.txt`; no external
  layout is produced. `files.sha256` lists every file of the entry but itself (the sorted
  `content/…` lines above, then `info.txt`, `os.ldr`); a content switch takes its `content/` lines. Each file is
  written as `<name>.part`, read back by SHA-256, `os.ldr` also checked by `start_verify`, then renamed,
  `os.ldr` last: an entry with an `os.ldr` is complete.
- **Into the library** (B after the job's last step, one request per menu-loop pass): the target is
  `mods-a` or `mods-b`, never the default or the boot-once entry (both taken:
  `mods.apply` answers `state`); of two, the absent one, else the one not written last this boot (B sees no
  file times), else `mods-a`. Order: rename `firmware/<target>` (if there) to `tmp/job-<id>-old`, rename
  `tmp/job-<id>` to `firmware/<target>`, remove the old tree; B's window close makes A rescan the library.
  A cut or a failure leaves the old entry (a failed rename in renames it back), the new one, or the
  target missing; never a part entry in `firmware/`. Debris stays in `tmp/job-*`: before a job's first step
  B removes `tmp/job-*` folders (never its own output), one list page and one unlink or rmdir per pass, at
  most 600 calls per sweep.
- **Trust** (packages are unsigned). A base is *known* when its stock, catalog and `edits.bin`
  SHA-256 and patcher numbers match an entry of `native/mod_known_bases.inc` (official open-xy bases;
  regenerate: `python3 -m xy_forever.modkit known-base BASE... --write`; `expanded_module_base` counts only when expanded); any other base is refused
  (`unverified`) unless the user confirmed it on the device, and the result says known or unverified.
  `start_verify` checks the base the same way, maps every edit into a stock record's payload and returns
  the image SHA-256, which the caller must compare with the hash recorded at assembly. The content step
  refuses a listed path in the firmware's `content.sha256` or a package id naming an official
  `presets|aux_presets/` folder (`official`), a machine package not in the link's selection (`unlinked`),
  and a `catalog.txt` over 512 bytes or 4 levels or whose number is not its top-level `"id"`.
