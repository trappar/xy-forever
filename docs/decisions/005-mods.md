# 005: Mods today

Layout reader/output and migration proposals below are superseded by
[035: browser onboarding only](035-layout-migration.md).

Research record, 2026-11-14, against the open-xy checkout `4718876` (branch `sdk/synth-postmix`, VERSION 0.0.4, clean); `git show origin/staging` (`fd66c4a`,
0.0.6) where noted. No device, no build, no network. **[C]** read from code/docs, **[I]** inferred, **[U]** unverified.

**Question:** what an open-xy build is, what "turn a mod on or off" can honestly mean on XY Forever, and what needs the open-xy maintainers.

## Answer

An open-xy build links the selected machines into one module of the user's stock 1.2.1 `os.ldr`; per-machine disable inside a linked `os.ldr` is
not possible safely. Build (a) + (a') now: open-xy builds as library entries, new combinations linked on the host. Ask the maintainers for (b), a
per-machine enable table, and build it when it ships. Defer (c), link at boot.

## What a build is

1. **Inputs** [C]: the user's decrypted stock `os.ldr` 1.2.1 (SHA-256 and size pinned in `profiles/opxy-1.2.1.json`; anything else, `.tfw` included, is
   refused), stock `content/op1_factory.json` (hash-pinned), a collection of machines (catalog entries pinned to repo/tag/commit, or local folders), optional
   extensions, and the previous `layout.json`. Two builders: `sdk.py build` (GNU ld, `tooling/native.py build`) and the browser builder
   (`builder/core.mjs linkKit` + `patchFirmware`) working on a kit (`opxy-browser-kit/1`: normalized ELF32 objects, profile, per-machine `absent` symbol maps),
   byte-identical to GNU ld by the export's own checks.
2. **Linker's job** [C]: place the selected machines' and SDK objects in one module at `module_base` `0x8d2d0380` (2 MiB reserved, 2032 KiB for code+data+BSS,
   16 KiB relocated stock tables); resolve 5 relocation types; set each machine's numbers as absolute link-time symbols (`<id>_engine_id`, `_page_id`, `_fx_id`;
   0 if deselected); alias a deselected machine's callbacks to `kit_absent`; apply the profile's edits (166 here, 217 on staging: hooks, table moves, heap
   shrink, startup label), skipping edits of unused services; append one loader record. The bootloader is never touched.
3. **Outputs** [C], builder ZIP: `os.ldr`; `content/op1_factory.json` (stock catalog plus one entry per machine at fixed offsets; an effect's entry carries its
   effect number); `content/display/<id>.svg`; `content/presets/<id>/*.json`; `layout.json`; `build.json` (`opxy-kit-build/1`: kit `version`, `source_sha256` =
   stock OS hash, `sizes`, SHA-256 of every file, `hardware_qualified:false`). `sdk.py build` writes `candidate/*`, `manifest.json`, `verification.json`,
   `layout.json`, `analysis/module.elf`.
4. **Identity** [C]: a machine is its `id` (`[a-z][a-z0-9_]{0,14}`, held for good by the catalog) and `kind` (`synth drum sample effect player`); its version
   exists only as the catalog's `tag`/`commit` (in the kit, not in any output); `machine.json` `sdk` is the SDK minimum. A build is its `os.ldr` hash, kit
   version (boot label `OPEN <version>-1.2.1`), profile, selection, extensions and layout. Base OS: only 1.2.1 (`source_sha256`).

Detail: `layout.json` and our parser, How a machine attaches, Proposed split in [005-mods.detail.md](005-mods.detail.md)

## On/off: what it can mean

| Level | What | Cost | Risk |
|---|---|---|---|
| (a) no open-xy change | Each build is a library entry; "on/off" = choose the entry whose selection you want. Per-machine disable inside a linked `os.ldr`: **not possible safely**: numbers are baked into instructions, registration is inline code, the builder ZIP has no symbol map [C] | small | content must match the entry (below) |
| (a') host relink, no open-xy change | the web app/host runs open-xy's own `linkKit` on the user's stock 1.2.1 + a kit for any selection and installs the result as a new entry [C: builder does this in-browser today] | medium, host only | one kit = one ABI generation; keep the user's layout |
| (b) build-time change | builder emits a per-machine enable table in the module (checked where `kit_absent` is used today) + a manifest with its file offset, before-bytes and machine order; XY Forever patches those bytes in its RAM copy after the CRC check, before boot [I] | small for both | memory not freed; disabled = absent (Prism rule); patching the image [U] |
| (c) link at boot | open-xy emits a device kit (binary objects, symbols, relocations, profile edits, absent maps, catalog/preset/artwork data); XY Forever reads stock 1.2.1 from the library, assigns numbers (FNV-1a + layout), links, applies the edits, writes content, boots [I] | large: a second builder in freestanding C++ | edit list churns (166 -> 217 in 2 weeks [C]); every SDK release re-issues every object; content writes still needed |

**Recommendation:** build (a) + (a') now: open-xy builds as library entries with a correct machine list, content handled per entry, new combinations linked on
the host. Ask the maintainers for (b) and build it when it ships: it is the cheapest true per-mod toggle on the device. Defer (c): no user gain over (a')+(b)
except offline arbitrary combinations, at the highest cost and risk.

**Package needs (input to decision 010).** Machine: catalog entry as is (`id kind maintainer status repository tag commit path cpu`) + `sdk` minimum + the
profile(s) it builds for. Missing: a machine version in build outputs; a profile/firmware field in the catalog (implicit today: the SDK supports only 1.2.1).
Firmware build: `build.json` + `layout.json` + files. Missing: per-selected-machine `{id, kind, tag, commit}` and profile id in `build.json`; one signed/hashed
manifest that names which files are content. Dependency: machine -> SDK >= `sdk`; SDK release -> profile (`opxy-1.2.1`) -> stock OS hash. A machine depends on
an OS version only through the SDK's profile [C].
## Asks for the maintainers

1. Write per-machine `{id, kind, tag, commit, sdk}`, `profile` and the extension list into `build.json` (and `selected` from `sdk.py build` too).
2. A per-machine enable table + manifest (option b), with its tests in the export checks.
3. Companions: keep an absent machine's reference across a save instead of writing Prism for good, and list which machines a project uses.
4. Answer: does stock 1.2.1 (and 1.2.5) tolerate `op1_factory.json` entries for machines the firmware lacks (synths: `ENGINE_SENTINEL` path drops them [C];
   effects carry an explicit number [U])?
5. Version the layout format when `machines` changes meaning; say whether `memory` builds stay.

**Unproven:** Superset content across entries [U]; RAM patching of an image before boot in our file-boot path [U]; stock's handling of an unregistered engine
number without a companion [U]; whether the 64-machine/8 KiB bounds fit real layouts [U].

**Rejected:** Patching a machine out of a linked `os.ldr` by pattern-matching (no symbols; a wrong guess crashes the OS and nothing proves a guess right).
Pre-linking every subset (2^n). Having the OS read a toggle file at startup (file I/O inside the stock initializer). Link-at-boot now (above).
