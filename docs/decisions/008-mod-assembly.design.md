# 008: On-device mod selection (design)

Detail of [008-mod-assembly.md](008-mod-assembly.md).

## Design
- **Packages (host tool, from an open-xy kit, no open-xy change):** `base` per kit generation: the profile edits in binary form, SDK and runtime objects in link
  order, definitions, `fixed` numbers, stock-catalog hash and insertion offsets, service fallbacks. One package per machine: its objects unchanged (ELF, so
  `core.mjs` can relink the same bytes), its `absent` map, kind and flags, link-order keys, its catalog entry with the number position marked, its finished svg
  and presets, and its source (repository, tag, commit) and licence. Kit sources: the published `kit.json` (catalog machines, already compiled), or
  `sdk.py kit --personal` for the user's own machines, which re-exports the whole generation. The device refuses packages of different generations. Cache:
  `/yaffs2/xy-forever/mods/<profile>/<generation>/` (~8.7 MB for the whole catalog [M]).
- **Assembly (Q3)**, on the processor that owns NAND (A) [I], as a job split into steps in its poll loop so link keepalives continue: (1) read the selection and
  the device layout; (2) number the machines (`numberLayout`); (3) read each object's headers and place sections text, rodata, data, bss in kit order from
  `0x8d2d0380`, within 2032 KiB; (4) build the global table, then the absent aliases; (5) copy each object's bytes into the module buffer and apply its
  relocations, checking each like `core.mjs` (opcode, ±16 MiB range, overlap, unresolved); (6) patch stock: services, hooks redirected to symbols, profiling off
  (the website's mode); append one loader record before FINAL; (7) self-check: undo the edits and remove the record, then the SHA-256 must equal stock;
  `native/ldr` inspect; (8) content delta (Q5); (9) admit (Q8). RAM ≈ 6 MiB of SDRAM, which is free while the menu runs [I]: module ≤ 2,080,768 B, stock
  2,373,336 B, one object ≤ 1 MiB, tables < 0.2 MB. It needs a memory-map entry clear of file staging (`0x8b800000..0x8c000000`) and the reporter. Time [I]: CPU
  < 0.1 s at 400 MHz for ≤ 54k relocations; reads ~4.4 MB at 4.9 MB/s [C] ≈ 1 s; writing ~2.5 MB plus content [U write speed]; so a few seconds to ~10 s.
- **Numbers and projects (Q4)** [C core.mjs/api.md]: one device layout per profile (`mods/<profile>/layout.json`, `opxy-layout/1`). It only grows: every number
  ever given out stays held, the 4 `fixed` machines keep theirs, and new machines get FNV-1a places in ID order. So a machine's numbers never depend on what
  else is selected. It is seeded from `/yaffs2/layout.json` (left by a web-builder install [C 006]) or from an imported entry's layout, so existing projects
  keep their numbers. It is written only after an entry is admitted. A project using an inactive machine opens with Prism (18), or no effect on a send, through
  open-xy's companion code; a save keeps Prism [C 005]. The number is never reused, so re-enabling the mod before saving restores the track. A notice in our
  menu is not planned.
- **Content (Q5)** [C `buildAssets`]: three things depend on the selection. `op1_factory.json` is the official file (hash-pinned) with each machine's entry
  inserted at 3 fixed offsets (engine 4435, fx 1583, seq 2565); an effect's entry carries its number, a player's carries the number + 4. Then `display/<id>.svg`
  and `presets/<id>/*.json`, prebuilt in the package. The assembled entry stores these as its content delta in `firmware/<id>/content/`, the same shape as an
  imported builder ZIP. The content switch applies it over the official content (#18) when the entry becomes the default, and removes files of mods no longer in
  the delta, as a package manager does. Stock's handling of catalog entries for absent machines stays decision 018's question [U].
- **Firmware versions (Q6)** [C]: each machine's code is compiled inside 1.2.1 platform templates (`voice.cpp.in` and others include `stock.hpp` with 17 stock
  addresses and `screen.hpp` with 15), and only the `opxy-1.2.1` profile exists. Packages are therefore per profile; the device cannot compile. The cache keeps
  compiled packages per profile plus each package's source identity, so a host rebuilds them for a new profile. After the OS version changes, the device
  re-assembles with that profile's packages, or says a host rebuild is needed. 1.2.5 needs open-xy's port (`docs/porting.md`, maintainer work with a device) and
  SAFETY's 1.2.5 rules first.

## Sizing, risks, proofs (Q9)
- **Device** (freestanding C++, fixed arrays, no heap) [I]: ELF subset reader ~200 lines; linker with runtime closure, placement, symbols, aliases and
  relocations ~350; numbering plus layout writer ~300; patcher plus self-check ~200; content delta ~150; job and admission ~200; Mods menu page ~250. **≈ 1,650
  lines**, plus ~600 lines of host tests. **Host** (Python) [I]: kit splitter ~350, push/apply/verify commands ~250, layout seeding ~50: **≈ 650 lines**.
- **Risks:** (1) generation coupling: machine objects only work with the SDK objects from the same export, so a non-catalog mod or a new SDK release means a
  host re-export of every package. (2) Format churn (166 → 205 edits in weeks [C]): the profile is data, but new edit kinds, relocation types or object rules
  need a device update; refuse unknown versions. (3) The ~6 MiB scratch region and keeping the job step-bounded on A. (4) Content consistency (decision 018).
  (5) Unverified write speed.
- **Provable on the host:** byte-identical module, layout, `os.ldr` and content against `core.mjs` (itself checked against GNU ld) for many selections, using
  the same C++ compiled for the host; numbering against `open-xy/tests/fixtures/numbers.json`; every rejection; a power cut at every write. **Device only:**
  memory fit, time, the file engine's writes, and that an entry assembled on the device and pulled back is byte-identical (the decisive test). Booting is then
  the same as booting a web-builder build.

## Reuse and licences (Q7)
The SDK, builder, linker script and profile are MIT [C `LICENSE`]. Porting `core.mjs` and the profile into XY Forever is allowed with the notice kept. Kit
notices [M] carry MIT (SDK and several machines), the GCC arithmetic runtime (GPL-3.0+ with the runtime exception) and one AGPL-3.0 text; Timestretch is AGPL,
and builds that include it are AGPL [C README]. Most machines' licences are not in the kit [U]. The catalog requires redistributable licences [C
`docs/catalog.md`]. For personal use nothing forbids this. If we ever publish packages, ship each one's notices and source pointer (repository and commit).
Nothing found forbids the approach.
