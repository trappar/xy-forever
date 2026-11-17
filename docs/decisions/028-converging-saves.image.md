# 028: Saves that converge (#27, research) (image)

Detail of [028-converging-saves.md](028-converging-saves.md).

## 1. Every place a mod is tied into the image
- **Its own bytes are in up to four ranges, not one.** `place()` (`native/mod_link.cpp:711`, = `linkKit` `core.mjs:765` [O]) lays out every linked object's
  sections group by group: all text, then rodata, data, bss. A machine has one range per group. Kit order puts all machines (orders 0-323) **before** the SDK
  (324-394) [C `base/objects.txt`]. With the default (packed) placement, removing `fm2op` (order 0) moves every later section, so every relocation changes. The
  edit idea needs placement as an input. A removal can still leave a hole and move nothing else.
- **SDK → machine.** Three SDK objects import each machine's callbacks and numbers: `startup.o`, `benchmark.o`, `project_companion_table.o`. That is 19
  relocation sites for `fm2op` (11 + 6 + 2 of 2,398) [M objdump]. An unselected machine's callbacks resolve to `kit_absent` (`absent.txt`, 426 rows, about 5 per
  machine). Its `<prefix>_engine_id/_page_id/_screen_page_id` symbols are 0 (`number_symbols`, `mod_link.cpp:762`; `aliases`, `:794`). Removing a machine means
  re-pointing those sites.
- **Machine → anything.** Machines import only SDK, stock (`definitions.txt`) and number symbols. A package object importing another package's symbol is refused
  (`foreign_import`, `pass2` `:918`) [C], so **no references between mods**. With the SDK pinned, a mod's relocated bytes depend only on its own address.
- **Shared services** (`base/services.txt`): 13 machines use them; `samples` alone is 145 KB, and samples + drums + octaves + drum_sends + drum_locks is 235 KB,
  mostly bss. A service has no owner. It is active while any selected machine (or extension) lists it (`prepare` `:539`). Only the last user's removal frees it.
  Then three things change: its objects go, its fallbacks (`fallbacks.txt`, 12 rows) are re-pointed in the SDK, and the service-gated **stock edits** switch off
  (`resolve_edits`, `mod_patch.cpp:365`). That last change rewrites bytes in the stock part of the file.
- **Runtime members** (`runtime_closure` `:662`, `selectRuntime` `core.mjs:715`): added while any linked object has a pending import. The set can shrink when a
  mod goes.
- **Hooks** (205 edits in `edits.bin`): they target SDK or service symbols only, never a machine's symbol [M strings]. With the SDK pinned they stay fixed,
  except for the service gating above.
- **Derived from the whole selection:** the module's record count, the first record's length and their XOR bytes (§2); `layout.json` (`selected`, `machines`
  numbers; `numbers::layout` keeps earlier numbers when given the device layout [C], so the other mods' numbers and catalog entries stay put);
  `content/op1_factory.json` (the official file with each machine's `catalog.txt` inserted at three pinned offsets, `mod_content.hpp`); `files.sha256`;
  `info.txt` `bytes/crc32/sha256`; `config.txt` `built`/`stamp`. The heap edits depend only on standard or expanded memory, not on the selection.
- **Content:** each mod's svg and presets (6 files on average). An aux preset path that two machines both carry is listed once, with the later machine's copy
  (`mod_content.hpp`).

## 2. The image container (`os.ldr` as we write it)
- Layout: stock 1.2.1 (984 loader records), the edits applied in place at fixed file offsets, then the module record replacing FINAL at `stock_bytes-16`, the
  module (initialized bytes only), then FINAL (`apply_edits`, `mod_patch.cpp:388`). Checksums: **each 16-byte header has an XOR byte, and nothing else does**.
  Payloads have no CRC (`header` `:38`). Lengths: the module record's `count` and the first record's `argument` (= total − 16). Whole-file SHA-256 and CRC-32
  exist only in `info.txt`, `files.sha256` and `config.txt`.
- A range inside the module can be rewritten without moving anything after it. A size change moves only FINAL (16 B) and two header words.
- **bss is free:** a stock zero-fill record covers the whole reservation before the module record loads (`core.mjs:922` [O]). So bss may sit anywhere outside
  the initialized range. Added initialized bytes cannot go after `data`, because bss starts there. Either they go into holes, or the layout keeps a gap
  (initialized from the bottom, bss from the top). A second module record is possible for the loader [I], but `start_verify` and open-xy's `recoverFirmware`
  expect one.
- **File level:** the job's file table has no seek (`mod_job.hpp` `Files`). B's client has `seek` (op 0x2c, `file_client.hpp:94`), and A's engine accepts a
  write open and lseek (`file_engine.hpp:47,83`). An in-place write into an existing yaffs file is **never tried on the device** [U]. Rewriting `os.ldr` in
  place in a live slot is not power-cut safe. On a copy it buys nothing, because the copy already writes every byte.
