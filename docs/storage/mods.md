# XY Forever storage layout: mod packages

Part of the [XY Forever storage layout](../STORAGE.md).

## External Open-XY layouts

`layout.json` belongs to Open-XY. XY Forever's firmware does not read, write or require
it. Leftover copies in existing entries are ignored. The package manifest supplies the optional
native machine count. The editable selection belongs to `config.json`.

Browser onboarding will read an existing layout's `selected`, `extensions`, `profile`
and memory choice, match them to native packages, show missing or incompatible mods,
and let the user review the native selection before rebuilding. It imports a mod
selection, never a compiled firmware image or a second runtime format. SDK layouts
without `selected` cannot recover a selection: `machines` includes historical number
reservations. This browser flow is planned in issue #94; regular users need no project
workspace, Python or CLI.

Upstream checked at `a07b08faeb58cc7e74cdbc8bd98f809a8ffd5159` (2026-11-17):
`tooling/machines.py:layout_document` and `builder/app.mjs`'s `nextLayout`.

## Mod packages (`mods/<generation>/`)

On the device a set lives in `mods/<generation>/` as a flat store, every file 4 folders below `/yaffs2`
(protocol 1.16, #124): [the store](mods-store.md). On the host, `modkit split` writes the tree below,
`DIR/mods/<profile>/<generation>/`.

Written on the host by `uv run -m xy_forever.modkit split` from an open-xy kit; local index + archives, `mods fetch`: `xy_forever/modindex.py`;
read by the device linker `native/mod_link` (B runs the assembly job). One flat folder per package: `base/`, `<machine id>/`,
`ext-<extension id>/` (`-` is not valid in an id, so names cannot collide). Each holds an
`manifest.json` or legacy `info.txt` and a sorted `files.sha256` of every other
file. The JSON record takes precedence when present and is checked under its
own name in the list. `<generation>` is `<kit version>-<12 hex>`: SHA-256
over the profile, every object's name, hash and tags, the collection, definitions, fixed numbers
and fallbacks. Text tables: `#` header, one record per line, fields
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
- **The linker** asks for whole files in this order: the base's metadata view, `services.txt`,
  `machines.txt`, `extensions.txt`, `fixed.txt`, `absent.txt`, `fallbacks.txt`, `definitions.txt`,
  `objects.txt`, `identities.txt`; each chosen package's metadata view and `objects.txt`; every object (pass 1, kit
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
