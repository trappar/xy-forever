# XY Forever storage layout: mod packages

Part of the [XY Forever storage layout](../STORAGE.md).

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

- **base** (`type=mod-base`, `needs_os` = stock `os.ldr` SHA-256, `profile`, `generation`, `source`, `licence`). Extra keys: `format`, `profile_sha256`,
  `module_base`, `module_bytes` (the meter's limit), `stock_bytes`, `edits`, `objects`, `machines`, `extensions`, `code`/`data`/`state` (SDK objects every build
  links), `runtime_*`, `catalog_sha256`, `catalog_engine_at`/`_fx_at`/ `_seq_at` (stock catalog insertion offsets), the patcher's `stock_records`,
  `reserved_bytes`, `heap_fill_base` (`0x` hex), `heap_fill_bytes`, `expanded_module_base` (the 4 MiB module's start, same end; older readers skip it) (32 keys:
  the reader allows 32). Files: `edits.bin`; `objects/sdk/…` unchanged with `objects.txt` (`<order> <role> <path>`, role `core`, `service=<name>` or `runtime`;
  link order is ascending `order` across all packages); `definitions.txt`; `fixed.txt`; `fallbacks.txt`; `services.txt` (users and the bytes the service adds);
  `machines.txt` and `absent.txt` (the whole collection: kind, prefix, requested numbers, features, origin, source, fallbacks); `extensions.txt` (link order,
  services, claims, `labels=` one per claim: percent-encoded UTF-8, only `A-Za-z0-9_.-~` kept, empty = none, `-` without claims; accessors, processor);
  `identities.txt` (`profile.identities`: `<rule> <value>`, each of `engines engine_none pages page_none effect_page` and the lists `stock_engines sample_pages
  effects players` once, comma-separated, `-` = empty); `linked.txt`; host-only `profile.json` (verbatim), `screen.svg`, `notices.txt`.
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
