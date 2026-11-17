# 005: Mods today (detail)

Detail of [005-mods.md](005-mods.md).

## `layout.json` and our parser

- Schema [C]: `{"format":"opxy-layout/1","profile":"opxy-1.2.1","machines":{id:{"engine"|"effect"|"player":n,"page":p}}}`; the builder adds `selected` (ids,
  collection order) and `extensions`; staging adds `memory:"expanded"` + `reserved_bytes` (experimental 4 MiB). Readers accept profile `opxy-1.2.0` too. The
  format string is the only version; staging still writes `/1` [C].
- `machines` is **every number ever given out**: the 4 fixed machines (`FIXED_NUMBERS`), dropped machines, and this build's (`numbers_after`) [C]. It is not the
  build's machine list; only `selected` is, and `sdk.py build` layouts have no `selected` [C]. Rely on [I]: `format`, `profile`, `machines` (numbers for
  companion mapping), `selected` when present. Ignore the rest.
- `target/xyf_layout.cpp` [C]: needs `format == opxy-layout/1`; keeps `profile` (unchecked); per machine needs exactly one of `engine`/`effect` plus `page`, so
  any **player** (`player` key) rejects the whole file; at most 64 machines, 8 KiB, ids <= 15 chars; skips unknown keys (so `selected`, `extensions`, `memory`
  parse but are ignored). The menu's count is `machines`, so it over-counts. Staging's catalog has 87 published machines (3 players) [C], so the 64 cap will be
  hit by long-lived layouts [I].

## How a machine attaches

- Code, data and state go in the module; the OS reaches it only through profile hooks: synth initializer exit and screen factories (registration), catalog
  insertion (menu names), sample/drum/player/LFO/controls hooks, project save/load (companions), preset scan [C, `docs/abi-1.2.1.md`]. Startup glue is generated
  straight-line code per collection (`startup.cpp.in`), not a table [C].
- Shared, fixed resources [C]: 2032 KiB module memory; engine numbers 34..255, pages 82..255, 39 effect numbers (1, 8, 11, 21, 23-28, 34-62), player numbers;
  effect state 476 B; 27 sounding voices; CPU (catalog `cpu.block_percent`, 3.5% advised); exclusive extension claims (Record double-tap).
- Projects store engine/effect **numbers** per pattern; presets store the machine **name** [C]. The companion `song.oxy` (`open-xy-project` v1, section
  `open-xy.machines` v1, bound to the `.xy` by size+CRC-32) maps numbers to ids on every load. It lists every machine the firmware holds, not the ones the
  project uses [C]. A machine the firmware lacks becomes **Prism (18)** on instrument tracks / no effect on sends, written into every pattern on load, with no
  notice [C]; a save then keeps Prism and the reference is gone [I].

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
