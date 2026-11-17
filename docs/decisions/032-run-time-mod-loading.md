# 032: Mods loaded from disk at run time (#28, research)

Research record, 2026-11-15; read-only, no device action. Open-XY from a fresh upstream clone (`paths.OPEN_XY_UPSTREAM`), `staging` tip **`ba943a8`** (fetched
2026-11-15, unchanged since record 028); paths below are in it unless they start with `native/` or `docs/`. Packages: the published 0.0.6 set
(`paths.PUBLISHED_MODS`; `info.txt` `code/data/state`). Tags: **[C]** our code, **[O]** open-xy, **[M]** measured, **[I]** inferred, **[U]** unknown.
**In brief.** Feasible in principle, and the stock OS already gives the decisive pieces: an engine is known to stock only through a 48-byte record that the SDK
*registers at run time* through stock's own registrar (factory and parameter-name pointers, name, ID, page), and a project hook already reads files on the GUI
role before a loaded project becomes active. But it is mostly **Open-XY runtime work**, the arena must stay a **reserved region** (not the stock heap), and the
selection limit becomes a per-project limit, not no limit. **Recommendation:** a first experiment with one machine, after the maintainers answer §8 Q1-Q4.

Detail: §1, §2, §6, §8 in [032-run-time-mod-loading.detail.md](032-run-time-mod-loading.detail.md)

## 3. Loading points
| Point | Hook exists | File access | User sees | Risk |
|---|---|---|---|---|
| Boot, all installed (synth initializer exit) | yes | [U] filesystem ready? | catalog 9.5 MB at ~2 MB/s: ~5 s more boot [I] | same limit as today; no gain |
| Project load, incl. workspace at boot (deserialize hook) | yes | yes, already used | median machine 59 KB: ~0.1-0.3 s for a typical project [I] | arena full or file bad → that machine stays a silent stub; must finish before the buffer goes live |
| Engine chosen from the menu/preset | **no** known site (only the preset *scan* is hooked) | GUI role reads the preset, so likely [I] | short pause on choosing | new reverse engineering + stock edit by Open-XY [U] |
| Factory / note on / audio block | yes | must not | glitches | **no-go** |
## 4. Memory
- **Not the heap.** Stock heap exhaustion is fatal, samples share it, and the 4 MiB option already warns of instability (`builder.md:211-213`). Heap code may
  also be out of reach: the heap spans about `0x8c0d0380-0x8d2d0380` (20 MiB ending at `RESERVATION_END`, `stock_tables.py:68`), and a Blackfin CALL reaches ±16
  MiB (our type 9/10 check, `native/mod_link.cpp:975-978`), so its low part cannot call stock code at `0x8d50xxxx` without veneers [I]. Free heap at run time is
  [U].
- **A reserved arena works:** keep today's 2/4 MiB reservation; the base (SDK, services, runtime, extensions, stub table) takes its bottom, the arena the rest.
  Benefit holds: install all 82 packages (9.5 MB on disk; code 2.4, data 4.5, state 2.8 MB), pay only for what one project uses. State (voice pools, BSS) is
  loaded memory too (`docs/api.md:38-47`).
## 5. Unloading
Stock keeps handles from earlier factory calls, and when it drops them after a project switch is [U]. **v1: never unload until reboot**; a boot loads only the
workspace's machines. Cost: switching between unlike projects fills the arena; when a machine no longer fits it stays silent and the user is told to restart.
Freeing needs a point after the new buffer goes live and stock released the old objects (Q4).
## 7. The loader
- **In the OS (Open-XY runtime), small:** read a per-machine file, check it, copy into an arena slice, apply relocations, flush data cache and invalidate
  instruction cache for the range ([U]: does stock have such a routine, Q5), fill the machine's slot, then the stub answers "ready". ~1-2 KB [I].
- **`native/mod_link` does not go into the OS** (51 KB, needs a 5.5-7.4 MiB workspace, record 028 §7). It stays in XY Forever as the **install-time preparer**:
  imports are only SDK, stock and number symbols, never another package (`foreign_import`, `mod_link.cpp:957`), so with the base pinned every import resolves at
  install time. The output per machine: initialized bytes linked at offset 0, BSS size, and a compact list of words to fix: type 18 (word) and 6/7 (half-words)
  add the load address; 9/10 (CALL/JUMP to base or stock) are recomputed from the load address (`mod_link.cpp:966-982`: our only four kinds).
## 9. Recommendation and steps (one builder each)
**A first experiment, not a build item yet.** Smallest proof: `sift` (synth, 32.6 KB, no own screen, no service) loaded from disk at project load, the rest
linked as today.
1. **Orchestrator:** send §8 Q1-Q4 to the maintainers; stop if Q2 has no answer and the menu path cannot be found (then the only gain is project-scoped
   loading).
2. **XY Forever, host only:** `mod_link` writes one machine's loadable at a fixed arena address; host test: equal to a full link placing it there.
3. **Test-only SDK fork** (private, from `ba943a8`, never shipped; or the maintainers): `sift`'s record served by a base stub; in the deserialize hook, if a
   pattern uses `sift`'s number, read the file into the fixed slice, flush caches, publish. One log line to a file.
4. **Device (user boots the test entry):** workspace with `sift` on track 1 plays; a project without it loads nothing; with the file removed, `sift` is silent
   and nothing crashes.
5. Then the relocatable list (§7) and the arena allocator; decide go on Q2/Q4.
**Plans.** Selection Saves disappear (everything installed is registered): #27 steps D (pinned placement/edit) and B (other slot as cache) become unnecessary;
content by difference still serves install/remove and should go ahead now, since this is months away. The meter becomes per project (loaded vs arena); the
64-machine limit is our reader's (`docs/storage/mods.md`, `layout.json`), stock room is 223 more engines and 44 effects. The content switch runs only on
install/remove.
**Not known:** Q1-Q7; free heap at run time; read speed of stock `fread`; how many machines have own screens that must register late.
