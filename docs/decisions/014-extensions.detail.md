# 014: Open-XY extensions (detail)

Layout reader/output and migration proposals below are superseded by
[035: browser onboarding only](035-layout-migration.md).

Detail of [014-extensions.md](014-extensions.md).

## Design
- **Package** `ext/<id>` (host packager, from the kit; no open-xy change): the object unchanged; its link-order key; `uses` (`extension`, plus `master_filter`
  for a processor; staging adds `drumline_page`); `exclusive` (claims; staging adds `drumline_page`); the accessor symbols to alias when off; name, description,
  note, origin, claim labels, actions; the processor's state id and SVG with its SHA-256; source (repository, tag, commit) from `catalog/extensions.json` at the
  kit's commit. **No edits, no addresses.**
- **Base** (as 008) also holds the extension order of its generation and the service objects above. Its edit list is the whole profile, unchanged.
- **Assembly**: services = 008's machine rule ∪ the `uses` of each selected extension, matched against `profile.services`. All else is 008's linker and patcher.
  The rule lives in host-made data, so the device needs no code for a new capability unless the capability brings new edit kinds or link rules.
- **Shared edits and order**: services are shared, never owned. One copy serves all subscribers and is applied once (union). `patchFirmware` refuses overlapping
  intervals across the whole profile, so no two services conflict. Edit order does not matter; object order is the kit's, and callback order is the
  collection's.
- **What "off" does** [C/I]: capture keeps no saved state, so Record double-tap is stock again. DJ filter: projects open with stock EQ (the stock EQ values stay
  in `.xy`). A save made while it is off drops the DJ choice and settings, because the SDK writes `open-xy.master-filter` only when a processor is linked [C
  `projects.md`; I]. Turning it back on before saving restores them. Drumline page (staging): pads play as stock; whether a save keeps their settings is [U].

## What changes in the mod-assembly steps (all in v1)
- **Packager:** extension packages, source pins from the catalog, `uses`/`exclusive`/accessor lists; the recombination test covers every `extensionChoices(kit)`
  (4 for 0.0.6). ≈ +100 lines Python.
- **Linker:** extension validation (ids, claims, collection order, ≤ 16), the object filter, absent-extension aliases, services from `uses`. ≈ +80 lines C++.
- **Patcher and content:** no new edit kind. The processor SVG goes into the entry's content delta as `display/<asset>`, with the asset-collision check. ≈ +30
  lines.
- **Job (#24):** the selection and the entry's inputs record extensions, and `layout.json` gets `extensions` (as the builder writes it; #22 keeps it). ≈ +20
  lines.
- **Mods page:** an Extensions section, a conflict message ("both use Record double-tap"), the meter counting the object plus first-use service bytes, extension
  ids in `xyf_host.py mods`. ≈ +80 lines + previews.
- **Byte check**: run `linkKit(kit, sel, layout, ext)`, `patchFirmware` and `buildAssets(…, ext)` for every extension choice crossed with every subset of the 7
  `linked` machines and with 008's 20 random selections. The module, `os.ldr`, layout and content must be identical. The kit's own export probably already
  proves `linked` + all its extensions against GNU ld [I: native builds include the whole collection, `docs/extensions.md`].

## What 008 needs to change
"No extensions" in the first version, and stage 2 "extensions as toggles", become stage 1 (stage 2 keeps rollback). The service rule gains the extension uses.
Absent aliases cover extensions too. Content gains the processor artwork. The kit has 79 machine entries [M] (008 says 80).

**Unproven:** `checkpoint` and `drumline_page` (not in the kit; staging's handling of an absent drumline processor not read); extension licences; DJ settings
dropped by a save while off [I]; whether boot once catches extension faults. **Rejected:** an extension package carrying its own edit list (the sites are shared
and SDK-owned, each applied once); applying the services always with a runtime enable flag (stock code changes and memory is used while off); patching an
extension into an assembled image; zero-machine builds in v1 (`linkKit` refuses them, so there is no reference to compare with). **Open questions:** (1)
extensions in the first version? (2) an extension needs at least one mod on: acceptable? (3) offer community extensions (DJ filter) beside Core capture?
