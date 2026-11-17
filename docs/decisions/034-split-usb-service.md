# 034: Split target/usb_service.cpp by includes

Decision record, 2026-11-17 (issue #43); a behaviour-neutral refactor, no device action.

## Question

`target/usb_service.cpp` was the largest C++ source in the repository (186 KB). How should it be split into smaller
files without changing the firmware, the builder's audits, the simulator or the tests? And is a code map wanted too?

## Answer

**An include split, now:** `usb_service.cpp` keeps the host-visible part, the device includes and the namespace and
`#if` scaffolding, and includes its parts (`usb_service_*.ipp`, one per section, each under 32 KB) exactly where their
text was. It stays one translation unit: the preprocessed unit is unchanged apart from line markers, and every build
mode's candidate SHA-256 is unchanged (`scripts/neutral.py`). Each part opens with a line saying it is a part of
`usb_service.cpp`, never compiled alone; each part balances its own braces and `#if`s. Tests read the file through
`xy_forever/source_text.py` `unit_text()`, which inlines the parts back into the exact text of before.

**Not separate translation units, for now.** The target build has no LTO, the helpers and shared state are file-local
(anonymous namespaces), and `xyf_build.py` routes defines by object name: separate objects change the firmware bytes,
so they need a device test, which waits for the device.

**No code map.** `grep -n` for function heads and the `// ----` section banners gives most of it; a C++ parser that
stays right is real work for a small gain.

Uncertain: whether line numbers moving between files reach any output (no `__LINE__`/`__FILE__` in the file, no `-g`
in the target flags; `neutral.py` showed none).

## Evidence

**Size.** 186,107 bytes, 3,336 lines: 2.5 times the next target file (`file_boot.cpp`, 73,184) and the largest C++
source (next: `native/mod_link.cpp` 65,780, `host/mod_assemble.cpp` 55,310). About 270 function bodies (brace scan),
2,695 lines inside them. Largest: `home_pump` 3147-3306 (160 lines), `mods_begin` 2181-2293 (113), `a_set_default`
2593-2662 (70), `install` 2524-2574 (51), `usb_begin` 2914-2962 (49). In the product's link map, `usb_service.o`
`.text` is 0xa708 (42,760) of the module's 0x35028; the module ends near 0x8ab42548 of the window to 0x8ab80000,
so code size is not a constraint either way.

**Sections** (the file's own `// ----` banners and command comments, lines before the split):

| Lines | What |
|---|---|
| 1-62 | host-visible part: `identity`, install admission (also compiled on the host by `tests/test_usb_service.py`) |
| 63-416 | device part (from `#if defined(__bfin__)` at 63): stock wrappers, CDC/composite transport, PUT state, watchdog guard, install log, HELLO, STATUS |
| 417-528 | stage 2 loader (ABI 3) |
| 529-1140 | file commands: READ_FILE, SCREEN, diagnostics, MTP self-test (625-731), SET_DEFAULT, LIBRARY_BOOT, RENAME, COPY, LIST, WRITE |
| 1141-1462 | mod assembly job on B (`mod_job::Files`, tree removal, commit) |
| 1463-1603 | save-time log |
| 1604-1959 | content switch (job kind 1), config job (kind 2) |
| 1960-2177 | MTP session |
| 2178-2460 | MODS_APPLY, the Mods page hooks (public, 2303-2405), JOB_STATUS, JOB_CANCEL |
| 2461-2576 | TFW_CHECK, INSTALL |
| 2577-2742 | public entry points (`a_set_default`, dry run, menu start), one link session (Test 065) |
| 2743-3071 | serial + MTP composite (record 031), progress marks (record 030) |
| 3072-3336 | home loop: `home_begin`, `home_pump`, `menu_release`, `reset_self` |

The cuts follow the scaffolding: `#if defined(XYF_USB_FILE_WRITE)` spans 974-2458, so the file commands split at 974
(the 35 KB section would be over the cap anyway), and the namespace switches at 2302-2306, 2405-2406 and 2575-2577 stay
in `usb_service.cpp`, which gives the Mods page hooks and JOB_STATUS/JOB_CANCEL their own parts.

**One translation unit in practice, not by a stated design rule.** The code relies on it: anonymous namespaces opened
at 85, 1964 and 2406 hold the helpers and the shared state (`result`, the parser, PUT state, guard words: initialized
`.data` globals, because the cold link has no BSS); the `stock<A, R, Args...>` call template (noinline, noclone,
line 90) is in that namespace, so each new translation unit would instantiate its own copies; one `#if
defined(XYF_SIM)` block (1961-1965) closes and reopens the anonymous namespace.

**How it is compiled and linked** (`xyf_build.py`): `cpp(name, path, defines)` compiles each object with the shared
flags (`-O2`, no LTO). `usb_service` is not in `GC_MODULES`, so it has no `-ffunction-sections`. It is linked only in
the product-kind modes (`xyf-product`, `xyf-mtp-usb`). Its defines are routed by name: probe defines, `hello_defines`,
`XYF_STAGE2_SERVICE`, `MTP_USB_DEFINES + MTP_SESSION_DEFINES`, `MTP_USB_TRIAL_DEFINES` apply only `if name ==
'usb_service'`. A new object not added there would compile without `XYF_MTP_USB` etc. and silently drop code behind
those guards. `module_objects` order is the link order, so a new object also moves the layout.

**Audits key on symbols, not file names.** `verify()` checks linked symbol names (`usb_service`, `usb_begin`,
`composite_turn`, `probe_spans`, `mtp_usb_command`, `serial_write`), `guard_pause` callers, and scoped calls by
namespace. The call audit and CRC spans (`usb_spans.hpp`) check call targets and stock addresses, not where the caller
lives. An include split passes them unchanged.

**Name-keyed consumers outside the builder**, and how the split covers them:
- `xy_forever/sim.py` copies only `target/*.?pp` (a `.ipp` part is copied); its `REWRITES` count matches per file
  (stock template, CYCLES, two FLUSH and one SSYNC now in `usb_service_transport.ipp`, IFLUSH and SSYNC in
  `usb_service_stage2.ipp`); the `__bfin__` widening and the asm check also cover the parts; `HELLO_HOOK` stays in
  `usb_service.cpp`.
- `own_sources()` hashed only `.cpp .hpp .s .py`: `SOURCE_SUFFIXES` now has `.ipp` and `.inc` too (the latter for
  `native/mod_known_bases.inc`, included by `mod_patch.cpp`, issue #80).
- 15 test files read `target/usb_service.cpp` as text (55 references): they read `unit_text()`.
- The host bridge (`tests/test_usb_service.py`) compiles only the host part; its cache follows `-M` dependencies.

## Rejected options

- **Separate translation units by section.** Changes the product candidates (no inlining across files, duplicated
  stock thunks, link order), exposes file-local state as global symbols, needs per-file define routing in
  `xyf_build.py` that fails silently when missed, and needs a device test for a refactor. Possible later, with the device.
- **No split, a code map (`scripts/codemap.py`, function line ranges) instead** (the research draft's answer). It keeps
  the 186 KB file and solves only navigation; grep for function heads and banners already gives most of it.
- **Leave it.** Every edit to the file pays grep plus guessed ranges on 186 KB, and nothing stops the next file growing
  the same way: `tests/test_source_budget.py` now caps source files at 32 KB.
