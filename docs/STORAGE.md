# XY Forever storage layout

Everything XY Forever owns lives under `/yaffs2/xy-forever/` (processor A's yaffs volume). Nothing outside
it is changed, except the boot partition (`os.ldr`) and the components an official update names.

```
/yaffs2/xy-forever/
  state-a.txt, state-b.txt  boot state, two power-safe slots (below)
  state.txt                 legacy single slot (read if no A/B slot is valid)
  firmware/<id>/os.ldr      one library entry's firmware image
  firmware/<id>/info.txt    its description (key=value, below)
  firmware/<id>/layout.json open-xy builds only: machines and numbers
  firmware/<id>/files.sha256, content.sha256 + firmware.version   optional lists (010)  [planned]
  mods/<profile>/<generation>/{base,<machine>,ext-<extension>}/   mod packages (below)  [host-made]
  log/                      device reports, prod-NNN-a.bin / prod-NNN-b.bin (from build 067)
  stage2/main.xys2          stage 2: B loads it at the first HELLO of a boot; see below
  inbox/                    MTP drop folder: .tfw, .ldr, open-xy bundles   [planned]
  tmp/job-<id>/             mods.apply's output until the commit; -old: the replaced entry; swept (below)
  content-state.txt         what /yaffs2/content/ matches, and what XY Forever installed there (below)
  content-official/         the official op1_factory.json and content.sha256, saved before the first content change
  config.txt                the saved configuration: base, mods on, what Open-XY was built from (below)
  settings.txt              device settings (#21, the Settings page; target/xyf_settings.hpp): confirm=0|1 stored for Confirm on device (not built); absent or unreadable: the defaults
```

Changes by hand (MTP, serial file commands; protocol 1.13) may touch all of `/yaffs2` except
`bootloader.dev` and `upgrade/` (the stock updater's inbox), both readable, never changed, renamed or
deleted, nothing written into or renamed onto either. Details and XY Forever's own narrower rules:
[SAFETY.md](SAFETY.md).

The reporter never creates `log/` (create it over USB first). Builds up to 066 wrote reports to
`/yaffs2/chained-loader/` (the project's old name), which may still exist.

`<id>`: 1–31 characters, lowercase letters, digits, `-` and `_`; also the folder name.

## Text format (`state-*.txt`, `info.txt`)

Line-based `key=value`, LF or CRLF line endings. A `#` at the start of a line
(after spaces) begins a comment. Keys are lowercase letters, digits and `_`, at
most 31 characters. Values are trimmed of surrounding spaces and tabs and are at
most 127 bytes. Files are at most 4 KiB. Two readers:

- **Lenient** (`xyf_store`: state slots, and `info.txt` in the library scan and
  the boot check): bytes are not checked, malformed lines are skipped, unknown keys
  ignored, the last of duplicate keys wins, longer names are cut to fit (47 / 31
  bytes).
- **Strict** (`xyf_manifest`: package admission and the inbox; not yet
  used by the library scan): the first problem refuses the whole file, with a
  status and its line. Refused: an empty file (no key line); no LF after the last
  line (a cut copy); a line over 255 bytes; any byte outside printable ASCII
  `0x20..0x7e` (a tab only around key, `=` and value; a CR only before LF); a line
  that is not blank, a comment or `key=value`; a bad key; any key given twice; more
  than 32 key lines; a known key with an empty or invalid value. Unknown keys are
  ignored (older builds read newer files) but count and are bounded as above.

### State slots

yaffs rename is not reachable at cold time, so writes alternate between
`state-a.txt` and `state-b.txt` and are read back. Each slot ends with `seq=<n>`
and a `crc32=<8 hex>` line covering every byte before it. The valid slot with the
higher `seq` wins; the legacy `state.txt` (no CRC) is used only when neither slot
is valid (`target/xyf_state.hpp`).

| Key | Meaning |
|---|---|
| `default` | `<id>` booted when no key is held |
| `once` | `<id>` booted on the next start only, then cleared |
| `seq`, `crc32` | slot sequence number and checksum (written last) |
| `stage2`, `stage2_previous`, `stage2_tries` | planned: current/previous stage-2 file and unconfirmed start attempts |

If `default` is missing or its entry is unusable, XY Forever opens its menu.

An entry is unusable without `os.ldr`, or when its content is not installed in `/yaffs2/content/`
(the one rule `xyf::library::content_installed`: the content state below matches it). Such an entry is
listed ("content not installed", catalog flag 8); PLAY (boot once) and `library.boot` refuse it
(`no_content`: make it the default to install its content); SHIFT+PLAY and `library.set_default` run the
content switch first and write the default only when it succeeded (`started`, then `job.status`).

### Content state

`/yaffs2/content/` is the OS's content; a mod firmware halts without its files, and a catalog naming mods
the firmware lacks breaks the OS's menus (decision 018), so it must match the booted entry.
`content-state.txt` (read by A's library scan, head only; written by B as `.part` and renamed):

| First lines | Meaning |
|---|---|
| no file | the official content, nothing installed |
| `state=official` | the same, after a switch back |
| `state=installed`, `entry=<id>` | content/ matches assembled mod set `<id>` (only it is usable) |
| `state=switching`, `target=<id>` | a switch did not finish: nothing is usable, the menu says "content needs repair" |

Then one `file=<path>` line per file XY Forever installed (relative to `/yaffs2/content/`, any order); in an installed record (#27) each is `file=<path>` TAB `<SHA-256 hex>`, sorted, the bytes
written. The switch writes by difference: a target file whose line in a trusted record (installed, sorted, another entry than the target) carries its SHA-256 is skipped unread; otherwise the live file
is read, and written only when missing or different. Switching to the installed entry again is the repair: it reads every file. A's engine never renames onto an existing file, so B replaces the record
as record -> `content-state.old`, `.part` -> record, `.old` removed; only a `.old` (a cut between) reads as switching, never as official. Stock entries (no `generation`) are usable only in the
official state; an assembled mod set only when the record is installed for it. A plain power-on never writes: an unusable default opens the menu with the reason ("Default <id>: content not installed"
or "content needs repair").

The switch (B's job, `target/xyf_content_job.hpp`; one file request per step, USB live): the target's `info.txt` (`generation`: a mod set), the record (or its `.old`); in the official state the live
catalog's SHA-256 must equal the `op1_factory.json` line of a library entry's `content.sha256` (else refused: not the official set), otherwise the saved list is used; the target's `content/` lines of
`files.sha256` (a path the official list names is refused, as is any path the rule refuses). Every refusal comes before the first write. Then: the official list and catalog saved under
`content-official/` (official state, unless the same copies are there); the record `switching` (the target's files and the previous ones still to remove); the previous files the target lacks removed
(with folders left empty, never an official one); the target's files written as `<path>.part`, read back, the old copy removed, renamed in; the catalog last (a stock target: the saved official one,
its SHA-256 checked); the record for the target. A failure or cut leaves `switching` (or the old or new record); running the switch again repairs it. Writes: only the record, the saved copies and, in
`/yaffs2/content/`, the catalog and paths from a mod set's `files.sha256` or the record, never `dlc/`, `user/` or a name holding `bootloader` (`xyf::content::allowed`, one function).

### Saved configuration

`config.txt` (`target/xyf_config.hpp`): `base`, `profile`, `generation`, `unverified`, `built=<base> <generation>
<set> <count>[ expanded]` (set: sum of the names' CRC-32), `slot` (the Open-XY entry), `stamp` (its `info.txt`
`sha256=`), `memory=standard|expanded` (Mod memory; anything else: standard), `mods` (last). Replaced as `content-state.txt` is; a reader takes `config.old` when the record is missing; no
record: no mods. Save builds into the spare slot unless `slot` exists, `built` matches and its `sha256=` equals
`stamp`; then the content switch (no default), the record, the default (one job, `config_store`).

`log/save-times.txt` (#27, `target/xyf_save_log.hpp`; read with `xyf_host.py save-times`): one line per
Save, USB build or content switch, the last 20; `kind result mods memory added removed total` then each
part's milliseconds and the switch's counts. Written by B in the last write window of the sequence (`.part`,
the old file removed, renamed); a failed write is ignored, a failed Save writes none.

### `info.txt` (package manifest)

Every package folder's manifest (decisions/010). Without `type` it is a firmware
entry: every `info.txt` written before `type` existed is one, unchanged.

| Key | Value (strict reader) |
|---|---|
| `type` | `firmware`, `mod-base`, `mod`; `companion` is read only to be refused; any other value is refused (`.xys2` and content packs have their own manifests: the `XYS2` header, the pack's `manifest.json`) |
| `name` | display name, at most 47 bytes, such as `OP-XY 1.2.5` |
| `version` | version text (display and equality only), at most 31 bytes |
| `kind` | `official`, `open-xy` or `custom` |
| `bytes` | size of `os.ldr`: decimal, no sign, no leading zero, below 2^32 |
| `crc32` | CRC-32 of `os.ldr`, 8 lowercase hex digits (checked before boot) |
| `sha256`, `needs_os` | 64 lowercase hex digits: the `os.ldr` SHA-256; the one this package needs |
| `content` | the content set's version (`content.sha256` holds the set), at most 31 bytes |
| `generation`, `profile` | kit export and OS profile (`opxy-1.2.1`): `[a-z0-9][a-z0-9._-]*`, at most 31 bytes |
| `machine` | `synth`, `drum`, `sample`, `effect`, `player` or `extension` |
| `source`, `licence` | `<repository>@<commit>` (127 bytes) and licence (63 bytes) |

| `type` | Required | Also allowed |
|---|---|---|
| none | nothing | the firmware keys |
| `firmware` | `name bytes crc32 sha256` | `version kind content source licence profile`, `generation` + `needs_os` (together: an assembled mod set) |
| `mod-base` | `profile generation needs_os` | `name version source licence` |
| `mod` | `name machine generation` | `version profile source licence` |

Any other known key for a type is refused. Dependencies are exact matches, checked
on the device: a base or an assembled set runs only on the entry whose `sha256` is
its `needs_os`; a mod's `generation` (and `profile`, if given) equals its base's; an
assembled set's `generation`, `needs_os` and `profile` equal its base's. Host twin:
`xy_forever/xyf_entry.py` (`parse_manifest`, `manifest_text`); `python3 -m
xy_forever library-entry` builds an entry; `xyf_host.py library-add` sends one.

### `files.sha256`

What a multi-file package owns, in `shasum -a 256` format: one line per file,
`<64 lowercase hex><space><space or *><path>` and LF (the last line too). Paths are
relative to the package folder, at most 160 bytes of printable ASCII without `\`
or `:`; no empty, `.` or `..` name, no name ending in `.` or space; never the list
itself or a name containing `bootloader`. Lines are sorted by path, bytewise
(`LC_ALL=C sort`), so a duplicate is always the line before: the device streams the
list with one line of memory, at most 4096 entries. Host: `files_sha256_text`.

`content.sha256` (a firmware entry's content set) is the same form, with paths relative to
`/yaffs2/content/`. `xyf_package.py content-manifest CONTENT --out DIR` writes it from an
official package's `firmware/blackfin/content/` with `content.sizes` (host only, `<bytes>  <path>`)
and `firmware.version`; `content-compare A B` (lists or folders) exits 0 when identical.

### Recognising a dropped file or folder (`xyf_manifest` recogniser)

Files, by their first 268 bytes and size, never by extension:

| Kind | Rule |
|---|---|
| official `.tfw` | `babecafe` + `beefcafe` at 0x40 (or a bare `beefcafe`), `TESG` at +0x84 of the crypto header, format words 1, 1 |
| LDR image | first block: XOR of its 16 bytes 0, byte 3 `0xad`, flags `0x5004`, count 0, length word = size - 16 (custom firmware or an XY Forever stage 1: not told apart) |
| stage 2 | `XYS2`, at least 96 bytes (`xyf_stage2` checks the rest) |

Stage 2 (`target/xyf_stage2.hpp` ABI 3): `stage2/main.xys2` is read by B's USB service at the first HELLO of a boot (never on a plain boot), checked (header, CRC, SHA-256, window `0x8ab90000..0x8ac00000` (since 171), `min_stage1` <= the stage-1 build) and copied to its load address; stage 2 makes no stock call and reaches stage 1 only through the append-only services table. Any failure leaves stage 1 as before (STATUS `usb_service.stage2` says why). Built by `xyf_build.py --xyf-product` as `main.xys2` beside `candidate.ldr`.
| ZIP | `PK\3\4` or `PK\5\6`: an open-xy builder ZIP, imported by the host only |

Folders, by their names and `info.txt`: `info.txt` decides by `type` (firmware
entries need `os.ldr`; one with `generation` is an assembled mod set and needs
`layout.json`); without it, `manifest.json` is a content pack and `os.ldr` a
firmware entry. `content.sha256` and `firmware.version` (a content set) come
together or not at all. Refused always: a name that is not one safe name (1-127
printable ASCII bytes, no `/`, `\` or `:`, not ending in `.` or space), any name
containing `bootloader` (case ignored), the companion images `*.cyacd` and
`ble.bin`, and `type=companion`. Anything else is unknown and refused.

## `layout.json` (open-xy)

XY Forever reads only `format` (it must be `opxy-layout/1`), `profile`, and the
machine IDs under `machines`. Each machine is an `engine` or an `effect`, with a
`page`. The reader is a bounded streaming scanner: at most 8 KiB, nesting at most
8 deep, and at most 64 machines. The format is defined by open-xy
(`../open-xy/tooling/machines.py`, `LAYOUT_FORMAT`).

Upstream's `machines` is a map of number reservations (it keeps disabled and fixed
machines); the enabled set is `selected`, and `extensions` is separate. Our reader
rejects `player` machines today, and larger collections may exceed its bounds.

## Mod packages (`mods/<profile>/<generation>/`)

Written on the host by `python3 -m xy_forever.modkit split` from an open-xy kit (008, 014); local index + archives, `mods fetch`: `xy_forever/modindex.py`;
read by the device linker `native/mod_link` (not in a build yet). One flat folder per package: `base/`, `<machine id>/`,
`ext-<extension id>/` (`-` is not valid in an id, so names cannot collide). Each holds an
`info.txt` the strict reader accepts (extra keys below are unknown to it and ignored) and a
sorted `files.sha256` of every other file. `<generation>` is `<kit version>-<12 hex>`: SHA-256
over the profile, every object's name, hash and tags, the collection, definitions, fixed numbers
and fallbacks. Tables (`*.txt` other than `info.txt`): `#` header, one record per line, fields
separated by one space, `-` for none.

- **base** (`type=mod-base`, `needs_os` = stock `os.ldr` SHA-256, `profile`, `generation`,
  `source`, `licence`). Extra keys: `format`, `profile_sha256`, `module_base`, `module_bytes` (the
  meter's limit), `stock_bytes`, `edits`, `objects`, `machines`, `extensions`, `code`/`data`/`state`
  (SDK objects every build links), `runtime_*`, `catalog_sha256`, `catalog_engine_at`/`_fx_at`/
  `_seq_at` (stock catalog insertion offsets), the patcher's `stock_records`, `reserved_bytes`,
  `heap_fill_base` (`0x` hex), `heap_fill_bytes`, `expanded_module_base` (the 4 MiB module's start, same end; older readers skip it) (32 keys: the reader allows 32). Files: `edits.bin`; `objects/sdk/…` unchanged with
  `objects.txt` (`<order> <role> <path>`, role `core`, `service=<name>` or `runtime`; link order is
  ascending `order` across all packages); `definitions.txt`; `fixed.txt`; `fallbacks.txt`;
  `services.txt` (users and the bytes the service adds); `machines.txt` and `absent.txt` (the whole
  collection: kind, prefix, requested numbers, features, origin, source, fallbacks); `extensions.txt`
  (link order, services, claims, `labels=` one per claim: percent-encoded UTF-8, only `A-Za-z0-9_.-~`
  kept, empty = none, `-` without claims; accessors, processor); `identities.txt` (`profile.identities`:
  `<rule> <value>`, each of `engines engine_none pages page_none effect_page` and the lists
  `stock_engines sample_pages effects players` once, comma-separated, `-` = empty); `linked.txt`; host-only `profile.json`
  (verbatim), `screen.svg`, `notices.txt`.
- **machine** (`type=mod`, `machine=<kind>`, `name`, `version` = catalog tag, `source` =
  `<repository>@<commit>`, `licence`: `unknown` unless the kit's notices carry one). Extra keys:
  `id`, `order`, `prefix`, `origin`, `objects`, `code`/`data`/`state`, `services`, `features`,
  requested `engine`/`effect`/`player` and `page`, `catalog_number=<start>,<end>` (the bytes of
  `"id": N` in `catalog.txt` the linker rewrites: effect number, or player number + 4), `asset`,
  `presets`, `maintainer`, `cpu_peak_percent`. Files: `objects/<id>/…` unchanged + `objects.txt`,
  `catalog.txt`, `content/display/<asset>` and `content/presets/<id>/…` (finished by `core.mjs`
  `buildAssets`, website colours), host-only `machine.json` (the rest of the kit entry), `NOTICE.txt`.
- **extension** (`machine=extension`). Extra keys: `id`, `order`, `link`, `services`, `claims`,
  `accessors`, `processor`, `state_id`, `asset`, sizes. Files: its object, `content/display/<asset>`
  (a master processor's artwork), host-only `extension.json`.
- **`edits.bin`** (little-endian): `XYFE`, u16 version (1), u16 edits, u16 services, u16 string
  bytes; one u16 string offset per service; the NUL-terminated strings; per edit u8 kind (1 heap,
  2 table, 3 text, 4 hook, 5 startup), u8 flags (1 call, 2 profiling-off hook, 4 has `after`), u8
  service (`0xff` none), u8 0, u32 address, u32 file offset, u16 length, u16 symbol string offset
  (`0xffff` none), `before`, then `after` when flagged. Edit names and `events` are not kept.
- **The linker** asks for whole files in this order: the base's `info.txt`, `services.txt`,
  `machines.txt`, `extensions.txt`, `fixed.txt`, `absent.txt`, `fallbacks.txt`, `definitions.txt`,
  `objects.txt`, `identities.txt`; each chosen package's `info.txt` and `objects.txt`; every object (pass 1, kit
  order); steps with no file that zero the module (128 KiB each); the linked objects (pass 2, link
  order). It does not read `files.sha256` (the caller
  checks it), `edits.bin` or content. A claim refusal's detail is the first chosen extension's label
  (`core.mjs` `claimLabel`).
- **Linker bounds `core.mjs` lacks** (packages are unsigned; it refuses what `core.mjs` would link):
  - a section, symbol or table name over 255 bytes: `name_length` (the 0.0.6 kit's longest is 182);
  - a name whose hash chain already costs 64 (1 per name, 16 per name with the same full hash): `name_chain`;
  - nothing to link: `empty` (`core.mjs` links the alignment padding alone if `module_base` is unaligned).
- What a machine or extension that is not chosen (its package may be absent) needs at link time
  is in the base: its numbers (0), fallbacks, accessors (aliased to `kit_absent`). A mod package
  holds no edits, definitions or absolute symbols, and its objects import only SDK and runtime
  exports (a machine also its own number symbols); `modkit` refuses anything else.
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
  output folder holds every `content/…` file above, `layout.json`, `info.txt`, `files.sha256` and
  `os.ldr`. `layout.json` is what open-xy's website writes (`builder/app.mjs`, `JSON.stringify(…, null,
  2)` + LF): `format`, `profile`, `selected` (the selection's order), `extensions` (collection order),
  `machines` (the link's numbers: fixed, layout, new), expanded: `"memory": "expanded"`, `reserved_bytes`. `info.txt`: `type=firmware`, `name` (the
  caller's), `kind=open-xy`, `bytes`, `crc32`, `sha256`, and the base's `needs_os`, `generation`,
  `profile`. `files.sha256` lists every file of the entry but itself (the `content/…` lines above, then
  `info.txt`, `layout.json`, `os.ldr`); a content switch takes its `content/` lines. Each file is
  written as `<name>.part`, read back by SHA-256, `os.ldr` also checked by `start_verify`, then renamed,
  `os.ldr` last: an entry with an `os.ldr` is complete.
- **Into the library** (B after the job's last step, one request per menu-loop pass; decision 019
  section 5): the target is `mods-a` or `mods-b`, never the default or the boot-once entry (both taken:
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
