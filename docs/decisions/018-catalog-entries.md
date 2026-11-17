# 018: Catalog entries for missing machines (research)

Research record, 2026-11-14, against open-xy `4718876`. Static only: OS images 1.2.1 (main), 1.2.5 and 1.1.33 from the private `firmware/decrypted`, linear
listings of 1.2.1, 1.2.5 and 1.1.33 (the maintainer's evidence, not shared), Ghidra decompiles of single functions (project copies). No device. **[L]** read
from listing or decompile, **[C]** read from open-xy code/docs, **[M]** measured on files, **[I]** inferred, **[U]** unknown. Addresses are 1.2.1 OS unless
noted.

**Question:** what the OS does with `op1_factory.json` entries, svgs and presets of machines the firmware lacks (and the reverse), whether it writes or caches
anything derived from them, and so whether `content/` must match the booted entry exactly.

## Answer in brief
Nothing fails at boot, but a superset is not safe in use. The OS loads the catalog once per boot without checks and builds its menus from it whatever the
firmware registers. Leftover entries show as menu items: every kind in an official entry; effects and players in an open-xy entry (its hook drops only
unresolved synth names). Choosing one does nothing useful at best; for a player, stock reads through a null pointer [L], a possible fault [I]. The OS keeps no
catalog-derived state and in practice writes nothing to `content/`. **Rule: strict.** Before an entry boots, `op1_factory.json` is exactly that entry's file and
`presets/<id>/` of machines it lacks are gone; leftover svgs are harmless but leave with their package.

## 1. Loading [L]
- db2 init `8d4e056c` (startup `8c0b792c`; 1.2.5 `8c0df14c`, 1.1.33 `8d4dae54`), after the `content` check and factory init:
  `8c042490("content/op1_factory.json", doc 0x08037fc4)` reads the whole file and parses it. Missing or malformed: an empty document, return value ignored, so
  boot continues with empty catalog menus [L; effect I]. No hash, schema, version, size or entry-count check; items go into growable vectors (36-byte items,
  28-byte names [C abi-1.2.1]). Read once per boot: a change takes effect at the next boot.
- Engine list `8d4e7f78`, rebuilt each time the menu opens: `type` `drum`/`drumline` first (1.1.33: `drum`/`dbox`), then the rest; number = the name looked up
  in the registered-engine map (`8d506cd8`), **33 ("none") if absent**. lfo `8d4e0dd0`, filter `8d4e0ea8`, fx `8d4e0f80`, seq `8d4e1054`: built once per boot by
  `8d4e08c4`, number = the entry's `id` as is. Insertion `8d5bd928` checks nothing. `*_old` lists are never read (no such string).
- Fields used: `type` (label and name), `id` (non-engine lists), `synth_only` (seq), `default_params` (engine by name, `8d4dec40`: absent = "failed to find
  engine type", defaults skipped; seq by `id`).
- Player choice `8c0af2a4`: finds the `seq_types` entry by `id`, maps its `type` through stock's 5-slot table `0x840ad588` (`8d5bde60`: arpeggio 2, hold 3,
  maestro 4, two empty slots), loads `default_params`. An unknown name is compared with the empty slots (`strlen(NULL)`), then gives type 0.

## 2. Absent machine, and the reverse
| Case | Official entry (no hooks) | Open-xy entry |
|---|---|---|
| synth/drum/sample entry, machine absent | listed with number 33 [L]; choosing it [U] | dropped: `extensions_catalog_checked` at `8d4e0bf6`/`8d4e0d98` returns 0 for 33 or an own machine not ready [C startup.cpp.in] |
| effect entry, id N absent | listed; send setter `8d50e740` refuses N ≥ 19, keeps the old effect [L] | listed (fx list not hooked); factory `8d506f10` finds no constructor, returns failure, no crash [L]; the send's state [U] |
| player entry, absent | listed; choosing it: null read, type 0 [L]; may fault [I] | same unless the build owns the name (`8d5bde60` hooked only with a player selected) [C] |
| machine linked, no entry | registered; reachable only via projects and presets [L] | same |
| no svg / no presets | n/a | synth page fails, engine not registered on the GUI role, entry dropped [C]; no presets: engine uses the entry's `default_params` [L] |
- Leftover svg: loaded lazily by fixed or registered names (`8d56d624` builds `content/display/<name>`); no directory scan, so an absent machine's file is never
  opened [L/I]. Leftover presets: the scan lists every preset with a `type` [C abi]; open-xy hides factory presets of uninstalled engines [C presets.cpp]; an
  official entry lists them and loading one fails (`8d4e556c` returns 0 for engine 33: "load patch failed") [L].
- Projects: stock's load check `8d588d80` sets an instrument track's engine to Prism (18) when `8d50534e` fails (number < 33 and record registered; open-xy
  raises the bound to 256), a send's effect to 0 when `8d5052fc` fails, player type > 4 or = 1 to 2 [L]. So 005's Prism fallback is stock's; open-xy's companion
  writes the same values for listed absent machines [C]. Same reset in 1.2.5 `8c186202`, 1.1.33 `8d576af2` [L, by pattern].

## 3. Writes and derived state
- 1.2.1 and 1.1.33 contain a catalog writer: flush `8d4e17b8` (1.1.33 `8d4dc09c`) re-serialises the document to `content/op1_factory.json` when dirty flag
  `0x08037fbd` is set (1 s debounce). Only the db2 setters `8d4e1648`/`8d4e16f8` with group 1 set it (`8d4dfc94`: 0 = `user/op1.json`, 1 = catalog); every
  direct caller passes 0, the one variable caller (`ctl_knob` `8c088d50`) uses a byte its constructor `8c088cbc` sets to 0 [L]. No path found; indirect calls
  not covered [U]. 1.2.5's flush `8c0e01e0` writes only `user/op1.json` [L].
- Otherwise `content/` is read-only to the OS (factory init copies out of it; "refusing to delete/move factory preset" [L 006]); 1.2.1 and 1.2.5 hold the same
  `content` strings [M]. Nothing derived is stored: menus are RAM vectors; `user/op1.json` holds settings (`knobs`, `global`, `sequencers`), no catalog [M];
  projects store numbers, presets names [C 005]. **Nothing to invalidate.**

## 4. Versions
1.2.5 = 1.2.1 in loading, lookup (33), lists and project reset; only the catalog writer is gone [L]. 1.1.33: same design and writer; its drum list is
`drum`/`dbox`, so 1.2.1 content puts `drumline` in its synth list as engine 33 and filter `dynamic` (18) in its filter list [L/I], a candidate cause of 006's
"1.1.33 pretty broken", not proven.

## 5. Design rule
- **Strict per entry.** The content switch (#18) makes `content/` equal the entry's set before it becomes the default: `op1_factory.json` byte-equal to the
  entry's (official 1.2.1/1.2.5: SHA-256 `36d14444…`; assembled: the patcher's), every `presets/<id>/` and `display/<id>.svg` of a mod outside the entry
  removed, the entry's added. A catalog is one whole file per selection, never merged. `user/` needs nothing.
- Smallest rule if a switch must be cheaper: catalog and `presets/` exact; svgs may stay [I]. A superset catalog is never safe (player rows).
- Drift: the content record's catalog hash (010) is checked at every switch; a 1.2.1 flush would rewrite the file with re-serialised bytes and must show as
  "content unknown", not as an error.

## 6. Check against 008
No contradiction. Offsets 4435 (the `\n\t]` closing `engine_types`, not `_old`), 1583 and 2565 hold for 1.2.1 and 1.2.5 (same file `36d14444…`); 1.1.33's
catalog (5,229 B) differs, so they do not apply there [M]. Player `id` = type + 4 matches stock (arpeggio 6 = type 2), and stock selects players by `id` [L].
Addition: stock never checks an effect's or player's `id` against its own list, so a collision would go unnoticed [I].

Detail: §7 Device check in [018-catalog-entries.detail.md](018-catalog-entries.detail.md)
