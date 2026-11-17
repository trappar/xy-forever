# 010: Package model (design)

Layout reader/output and migration proposals below are superseded by
[035: browser onboarding only](035-layout-migration.md).

Detail of [010-package-model.md](010-package-model.md).

## Layout (keeps every existing path; nothing to migrate) [D]
```
/yaffs2/xy-forever/
  state-a.txt, state-b.txt  boot slots, unchanged (default, once, stage2, stage2_previous, stage2_tries) [C]
  firmware/<id>/            entry: info.txt, os.ldr, [layout.json, files.sha256, content.sha256 + firmware.version (its set), content/ (its overlay)]
  mods/<profile>/           layout.json (device numbering, 008) and <generation>/{base,<machine>}/
  blobs/<sha256>            content files a kept manifest names that are not in /yaffs2/content now  [new]
  inbox/                    drop folder for both doors and the menu: .tfw, .ldr, .xys2, package folders [planned]
  tmp/                      uploads (009), job staging, removals; swept when no job runs             [009]
  content.txt, content.list, job.txt   the content record and the content switch's journal (below)  [new]
  settings.txt (#21), stage2/{main,previous}.xys2, log/                                             [planned, C]
```
Outside: `/yaffs2/content/` and `/yaffs2/firmware.version` (written only by the content switch; `content/dlc/` only by pack install/remove);
`/yaffs2/layout.json` (read once to seed `mods/<profile>/layout.json`, 008). Never: `bootloader.dev`, `user/`, `/fat32`, `upgrade/`, the boot partition (except
`xyf-stage1`), SPI, OTP, companions. Existing devices: an `info.txt` without `type` is a `firmware` entry; entries without `content.sha256` show "content
unknown" until the host adds its content manifest; new folders are made on first use.

## Manifest: `info.txt` key=value [D]
The existing reader [C `xyf_store.cpp`: 4 KiB, values <= 127 B, unknown keys ignored, so older builds still read new files]. JSON rejected: the device has only
the layout scanner. Multi-file packages add `files.sha256` in `shasum -a 256` format (`<hex>  <relative path>`, streamed line by line; anyone can make or check
it). Dependencies are exact matches only, no ranges.
```
# firmware/opxy-125/info.txt               # mods/opxy-1.2.1/<gen>/fm2op/info.txt (+ files.sha256: objects, svg, presets)
type=firmware                               type=mod
name=OP-XY 1.2.5                            name=FM2OP
version=1.2.5                               version=v0.3.0            (catalog tag; display and equality only)
kind=official                               machine=synth
bytes=<os.ldr size>                         generation=<kit export id>  (must equal the base's)
crc32=<8 hex>        (boot check, as today) source=<repository>@<commit>
sha256=<64 hex>      (what a needs_os names) licence=MIT
content=1.2.5        (content.sha256 holds the set)   # base: type=mod-base, profile=opxy-1.2.1, generation=, needs_os=<stock 1.2.1 sha256>
```
An assembled entry adds `generation=`, `needs_os=` and keeps its selection in `layout.json` `selected` [C 005]. **Before accepting** (any door): path policy
(009); known, allowed type; every listed size, SHA-256 and the `os.ldr` CRC-32 + `ldr::inspect` [C]; `.tfw`: 003's checks incl. signature; `.xys2`: header
checks [C]; needs met; id free (an install never overwrites); space. Unsigned folders are accepted (user decision): hashes prove integrity, not origin. **A
plain copy** into `inbox/` is recognised by shape: `.tfw` magic, LDR (`ldr::inspect`), `XYS2`, a folder with `info.txt` (its `type`), a folder with a stock pack
`manifest.json`. Without `files.sha256` the device hashes at install and writes the list into the installed folder; a folder copied straight into `firmware/`
works as today.

## Jobs (004: one at a time, progress, `job.status`) [D]
- **install** (inbox item): checks above -> build in `tmp/<job>/` (a `.tfw`: entry + `content.sha256` from the ZIP, blobs for content files not already in
  `content/` or `blobs/`) -> rename blobs into `blobs/` -> rename the entry onto `firmware/<id>` (the commit) -> delete the inbox file. Cut before the commit:
  debris and unreferenced blobs (collected later); after: installed, inbox file left ("already installed").
- **remove**: not the default or current stage 2; confirmation lists dependents -> rename into `tmp/` -> delete -> collect blobs no kept manifest names. Packs:
  delete `content/dlc/<name>/`.
- **activate** (set default): same content set and overlay as `content.txt` -> slot write only (today). Else journal -> **content switch** (desired = the
  entry's set, overlay files win by path; all sources checked before the first write; a replaced or dropped file a kept manifest names is moved to `blobs/`,
  others deleted; new files copied from `blobs/` or the entry via `tmp/`; `firmware.version` last) -> slot write -> record -> delete journal. Files in no record
  are left and reported; a full reinstall removes them, as stock (packs kept).
- **apply mods** = #24's assembly into the non-default A/B id (one rename commit), then activate. **switch content / repair** = activate's switch for the
  current default.
