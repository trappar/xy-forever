# 005: Mods today

Research record, 2026-11-14, against the open-xy checkout `4718876` (branch `sdk/synth-postmix`, VERSION 0.0.4, clean); `git show origin/staging` (`fd66c4a`, 0.0.6) where noted. No device, no build, no network. **[C]** read from code/docs, **[I]** inferred, **[U]** unverified.

**Question:** what an open-xy build is, what "turn a mod on or off" can honestly mean on XY Forever, and what needs the open-xy maintainers.

## What a build is

1. **Inputs** [C]: the user's decrypted stock `os.ldr` 1.2.1 (SHA-256 and size pinned in `profiles/opxy-1.2.1.json`; anything else, `.tfw` included, is refused), stock `content/op1_factory.json` (hash-pinned), a collection of machines (catalog entries pinned to repo/tag/commit, or local folders), optional extensions, and the previous `layout.json`. Two builders: `sdk.py build` (GNU ld, `tooling/native.py build`) and the browser builder (`builder/core.mjs linkKit` + `patchFirmware`) working on a kit (`opxy-browser-kit/1`: normalized ELF32 objects, profile, per-machine `absent` symbol maps), byte-identical to GNU ld by the export's own checks.
2. **Linker's job** [C]: place the selected machines' and SDK objects in one module at `module_base` `0x8d2d0380` (2 MiB reserved, 2032 KiB for code+data+BSS, 16 KiB relocated stock tables); resolve 5 relocation types; set each machine's numbers as absolute link-time symbols (`<id>_engine_id`, `_page_id`, `_fx_id`; 0 if deselected); alias a deselected machine's callbacks to `kit_absent`; apply the profile's edits (166 here, 217 on staging: hooks, table moves, heap shrink, startup label), skipping edits of unused services; append one loader record. The bootloader is never touched.
3. **Outputs** [C], builder ZIP: `os.ldr`; `content/op1_factory.json` (stock catalog plus one entry per machine at fixed offsets; an effect's entry carries its effect number); `content/display/<id>.svg`; `content/presets/<id>/*.json`; `layout.json`; `build.json` (`opxy-kit-build/1`: kit `version`, `source_sha256` = stock OS hash, `sizes`, SHA-256 of every file, `hardware_qualified:false`). `sdk.py build` writes `candidate/*`, `manifest.json`, `verification.json`, `layout.json`, `analysis/module.elf`.
4. **Identity** [C]: a machine is its `id` (`[a-z][a-z0-9_]{0,14}`, held for good by the catalog) and `kind` (`synth drum sample effect player`); its version exists only as the catalog's `tag`/`commit` (in the kit, not in any output); `machine.json` `sdk` is the SDK minimum. A build is its `os.ldr` hash, kit version (boot label `OPEN <version>-1.2.1`), profile, selection, extensions and layout. Base OS: only 1.2.1 (`source_sha256`).

## `layout.json` and our parser

- Schema [C]: `{"format":"opxy-layout/1","profile":"opxy-1.2.1","machines":{id:{"engine"|"effect"|"player":n,"page":p}}}`; the builder adds `selected` (ids, collection order) and `extensions`; staging adds `memory:"expanded"` + `reserved_bytes` (experimental 4 MiB). Readers accept profile `opxy-1.2.0` too. The format string is the only version; staging still writes `/1` [C].
- `machines` is **every number ever given out**: the 4 fixed machines (`FIXED_NUMBERS`), dropped machines, and this build's (`numbers_after`) [C]. It is not the build's machine list; only `selected` is, and `sdk.py build` layouts have no `selected` [C]. Rely on [I]: `format`, `profile`, `machines` (numbers for companion mapping), `selected` when present. Ignore the rest.
- `target/xyf_layout.cpp` [C]: needs `format == opxy-layout/1`; keeps `profile` (unchecked); per machine needs exactly one of `engine`/`effect` plus `page`, so any **player** (`player` key) rejects the whole file; at most 64 machines, 8 KiB, ids <= 15 chars; skips unknown keys (so `selected`, `extensions`, `memory` parse but are ignored). The menu's count is `machines`, so it over-counts. Staging's catalog has 87 published machines (3 players) [C], so the 64 cap will be hit by long-lived layouts [I].

## How a machine attaches

- Code, data and state go in the module; the OS reaches it only through profile hooks: synth initializer exit and screen factories (registration), catalog insertion (menu names), sample/drum/player/LFO/controls hooks, project save/load (companions), preset scan [C, `docs/abi-1.2.1.md`]. Startup glue is generated straight-line code per collection (`startup.cpp.in`), not a table [C].
- Shared, fixed resources [C]: 2032 KiB module memory; engine numbers 34..255, pages 82..255, 39 effect numbers (1, 8, 11, 21, 23-28, 34-62), player numbers; effect state 476 B; 27 sounding voices; CPU (catalog `cpu.block_percent`, 3.5% advised); exclusive extension claims (Record double-tap).
- Projects store engine/effect **numbers** per pattern; presets store the machine **name** [C]. The companion `song.oxy` (`open-xy-project` v1, section `open-xy.machines` v1, bound to the `.xy` by size+CRC-32) maps numbers to ids on every load. It lists every machine the firmware holds, not the ones the project uses [C]. A machine the firmware lacks becomes **Prism (18)** on instrument tracks / no effect on sends, written into every pattern on load, with no notice [C]; a save then keeps Prism and the reference is gone [I].

## On/off: what it can mean

| Level | What | Cost | Risk |
|---|---|---|---|
| (a) no open-xy change | Each build is a library entry; "on/off" = choose the entry whose selection you want. Per-machine disable inside a linked `os.ldr`: **not possible safely**: numbers are baked into instructions, registration is inline code, the builder ZIP has no symbol map [C] | small | content must match the entry (below) |
| (a') host relink, no open-xy change | the web app/host runs open-xy's own `linkKit` on the user's stock 1.2.1 + a kit for any selection and installs the result as a new entry [C: builder does this in-browser today] | medium, host only | one kit = one ABI generation; keep the user's layout |
| (b) build-time change | builder emits a per-machine enable table in the module (checked where `kit_absent` is used today) + a manifest with its file offset, before-bytes and machine order; XY Forever patches those bytes in its RAM copy after the CRC check, before boot [I] | small for both | memory not freed; disabled = absent (Prism rule); patching the image [U] |
| (c) link at boot | open-xy emits a device kit (binary objects, symbols, relocations, profile edits, absent maps, catalog/preset/artwork data); XY Forever reads stock 1.2.1 from the library, assigns numbers (FNV-1a + layout), links, applies the edits, writes content, boots [I] | large: a second builder in freestanding C++ | edit list churns (166 -> 217 in 2 weeks [C]); every SDK release re-issues every object; content writes still needed |

**Recommendation:** build (a) + (a') now: open-xy builds as library entries with a correct machine list, content handled per entry, new combinations linked on the host. Ask the maintainers for (b) and build it when it ships: it is the cheapest true per-mod toggle on the device. Defer (c): no user gain over (a')+(b) except offline arbitrary combinations, at the highest cost and risk.

**Package needs (input to decision 010).** Machine: catalog entry as is (`id kind maintainer status repository tag commit path cpu`) + `sdk` minimum + the profile(s) it builds for. Missing: a machine version in build outputs; a profile/firmware field in the catalog (implicit today: the SDK supports only 1.2.1). Firmware build: `build.json` + `layout.json` + files. Missing: per-selected-machine `{id, kind, tag, commit}` and profile id in `build.json`; one signed/hashed manifest that names which files are content. Dependency: machine -> SDK >= `sdk`; SDK release -> profile (`opxy-1.2.1`) -> stock OS hash. A machine depends on an OS version only through the SDK's profile [C].
## Asks for the maintainers

1. Write per-machine `{id, kind, tag, commit, sdk}`, `profile` and the extension list into `build.json` (and `selected` from `sdk.py build` too).
2. A per-machine enable table + manifest (option b), with its tests in the export checks.
3. Companions: keep an absent machine's reference across a save instead of writing Prism for good, and list which machines a project uses.
4. Answer: does stock 1.2.1 (and 1.2.5) tolerate `op1_factory.json` entries for machines the firmware lacks (synths: `ENGINE_SENTINEL` path drops them [C]; effects carry an explicit number [U])?
5. Version the layout format when `machines` changes meaning; say whether `memory` builds stay.

**Unproven:** Superset content across entries [U]; RAM patching of an image before boot in our file-boot path [U]; stock's handling of an unregistered engine number without a companion [U]; whether the 64-machine/8 KiB bounds fit real layouts [U].

**Rejected:** Patching a machine out of a linked `os.ldr` by pattern-matching (no symbols; a wrong guess crashes the OS and nothing proves a guess right). Pre-linking every subset (2^n). Having the OS read a toggle file at startup (file I/O inside the stock initializer). Link-at-boot now (above).

## Proposed split

| Step | Done when |
|---|---|
| #22 build: layout reader v2 | accepts `player`, keeps `selected`/`extensions`, ignores `memory`; machine list = `selected` (else "numbers only"); bounds 256 machines/16 KiB if scratch allows; profile must be `opxy-1.2.1`/`opxy-1.2.0`; synthetic builder- and sdk-shaped fixtures; tests pass; product builds and verifies |
| build: host import | `xyf_host.py library-add --open-xy <zip>`: checks `build.json` hashes and `source_sha256`, writes `info.txt` (`kind=open-xy`, version from kit), `layout.json`, `build.json`, content under `firmware/<id>/content/`; simulator tests |
| build: menu | an open-xy entry shows its machines and version; `ui_preview.py` screens; tests |
| research + device safe: content per entry | superset vs swap decided with ask 4; one device check that an extended `op1_factory.json` with an absent machine boots stock 1.2.1 and an open-xy entry (backup restored) |
| build: content switch | on an explicit default change only, via `native/content` generations, stock content kept; fault tests |
| device safe: two mod sets | two open-xy entries + official 1.2.1; switch from the menu; each shows its machines; no project saved |
| build: per-mod toggle | blocked on ask 2; split when the manifest exists |
