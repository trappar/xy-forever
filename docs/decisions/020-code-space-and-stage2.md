# 020: Code space and stage 2

Research record, 2026-11-14: build 158's `module.map` and `manifest.json`, objects compiled with the product flags in a temporary worktree (removed), an uncommitted linker snapshot of `native/mod_*`, an earlier update study (the maintainer's, not shared). No device. **[C]** read from code, map or listing, **[M]** measured, **[I]** inferred, **[U]** unknown, **[D]** proposal.

**In brief.** The 24,248 B is a builder constant, not a hardware limit. The planned features need ~105-140 KB, five times that. Stage 1 runs out at MTP or at the package jobs (#11, #12), whichever lands first after M2. Mod assembly cannot fit: the unfinished linker alone is 29.6 KB. Stage 2 is built (header, loader, ABI 1/2), but only in two obsolete offline modes, on B only, never run on the device, with no tries or rollback. Recommendation: every feature after the file engine goes into stage 2, on both processors; stage 1 owns the loop and every stock call; build the stage-2 steps (section 7) right after the lone-restart fix (016), before MTP and #11. No new stock call.
## 1. Stage-1 limits (A and B run the same image: every bound and every byte counts on both [C])
| Bound | Value | Binding |
|---|---|---|
| Builder `layout()` (wide): code `0x8ab00000..0x8ab1c000` = 114,688 B, 16 KiB stack to `0x8ab20000`; linker `ASSERT(__end <= limit)`, `verify_elf` [C] | 158: text 74,088 + rodata 11,265 + data 5,064 + pad = 90,440 B, **24,248 B free** [M] | yes, the only one |
| Boot partition (capacity read at install, `889f65f0`) [C] | 3.4-3.5 MB images installed and booted [C HARDWARE, 012]; exact size [U] | no (outer `os.ldr` 90,512 B) |
| Real bootloader: stream at `0x8a000000..0x8a800000`, records inside `ALLOWED` [C `outer.py`, `cold.cpp`] | 8 MiB | no |
| Above the stack: stage-2 window `0x8ab20000..0x8ac00000` (896 KiB), `0x8ac00000..0x8b000000` unreferenced (4 MiB) [C 019]; no static stock segment (`check_boot_reservations`) [C]; stock use at cold time never tested by a write [U] | 4.9 MiB | no |
| Cold LDR policy protects `0x8ab00000..XYF_STACK_END` from library images; the window is not protected [C `cold.cpp`]; the OS loads elsewhere [M 019] | | no |

**Raising it** is cheap in code: `WIDE_STACK(_END)`, the window in `xyf_stage2.hpp`/`.py`, `tfw_extract.py`, `sim.py`, layout tests; the entry stack and protected range follow `XYF_STACK_END` [C]. Risks: SDRAM never loaded at power-on (as the 043 widening was) [U]; each stage-1 KB costs ~0.27 ms on every boot (B enters at 1.07 s with 77 KB, 1.98 s with 3.5 MB [C HARDWARE]; linear [I]); every change is a boot-partition write with no embedded fallback since 088. A reserve lever (+32 KiB), not the plan.
**Top consumers [M map]:** usb_service 14,306; peer_services 9,688; file_boot 8,852; storage_server 7,365; cold_storage 5,879; tfw.check group 8,989 (p224 3,468, tfw_check 1,984, tfw 1,960, aes 1,052, otp_keys 312, tfw_signature 213); menu and UI 6,700; library and state 5,725; file_client 2,624; peer_boot 2,600; diagnostics (census, trace_slot*, warm_snapshot, record) 2,582. **Reclaimable without losing function [I]:** tfw.check into stage 2 (8,677 B; `otp_keys` stays as a service) and the second CRC-32 table (`inflate.o` 1,024 B rodata beside `xyf_crc32`'s, used by peer_boot): ~9.7 KB. Diagnostics only by dropping the cold report.
## 2. Costs per plan item (KB; one image, so both processors; objects before section GC are upper bounds: `tfw.o` 2,636 B, 1,960 linked [M])
| Item | KB | Basis | Free after |
|---|---|---|---|
| lone-restart (016), #2 and file-engine fixes; window, #4 streaming and commit | 5-6.5 | fixes ~1.5 [I]; file_engine 1.2, file_client 2.6 [M] | ~18.5 |
| serial file commands (B) | 2-3 | handlers in usb_service [I] | ~16 |
| #8 writer on A, B path removed | 2-3 | 012: ~300 lines at 9-15 B/line [C 019] | ~13.5 |
| #9 USB driver + MTP responder (B) | 11-17 | ~1,000-1,400 lines [I] | **out** |
| strict reader, file lists, recogniser | 6.3 | 3,596 + 892 + 1,836 B [M] | |
| #11, #12 commit, jobs, journal, repair, queries | 5-7 | [I] | |
| #14 `.tfw` install (B decodes, A hashes) | 9-11 | tfw_os 3,919, full inflate +2,544, package_policy 1,660 [M]; job [I] | |
| #18, #20 content switch and packs (A) | 9-13 | content 2,008, package 2,556, zip 2,492 [M]; job [I] | |
| stage-2 loader, trial, uninstall row; #21 settings + prompt; #22 layout v2, machines | 6.5-9 | xyf_stage2 588 [M]; `draw_confirm` exists [C]; [I] | |
| linker, patcher, job (A; #24), controller and page (B) | 40-50 | WIP linker 29,648 B (mod_link 22,432, mod_numbers 4,288, mod_elf 2,928) [M]; rest [I]; 019's 9-15 KB is low | |
| #31, #33 companion query, Bluetooth updater (B) | 8-12 | companions 1,764, ble_session 1,584, images 1,196 [M]; 015: 600-900 lines [I] | |
## 3. Stage 2 today
- **Built [C]:** header `XYS2`, 96 B (abi, load and entry, size, CRC-32, SHA-256, `min_stage1`, version, build id, header CRC; `validate`/`accept`/`classify`); packer `xy_forever/xyf_stage2.py`; images `stage2_hello.cpp` (ABI 1) and `stage2_menu.cpp` (ABI 2: returns requests, stage 1 performs boot once and set default); trampoline `xyf_stage2_enter.s`, the one register jump the call audit admits; `check_stage2_image` (no BSS, entry at the window, no call or high-half address outside the image); host tests `test_xyf_stage2*.py`.
- **Load [C `file_boot.cpp`]:** only with PLAY, after the catalog. B sends item `0x12`; A reads `/yaffs2/xy-forever/stage2/main.xys2` read-only into staging, `accept`s it (with SHA-256), serves it or an 8-byte `XYSN` refusal; B (15 s bound) re-runs `classify`, copies to `0x8ab20000`, re-checks the CRC, FLUSH/IFLUSH, calls it on its stack with 4 services (frame begin/end, next event, ms); it calls no stock code. Missing or corrupt: the stage-1 menu. Link lost: "boot built-in", gone since 088.
- **Only named:** state keys `stage2`, `stage2_previous`, `stage2_tries` (STORAGE, DESIGN; `xyf_state` has none), the tries fallback, `previous.xys2`, an A part, the `xyf-stage2` install, crash handling (a hang today meets only the guard, 016). All code sits in the pre-v3 branch (not `XYF_ONE_SESSION`) of `--xyf-stage2(-menu)`, 48 KiB layout; the product has none. **Never ran on the device** (HISTORY, FINDINGS-stage2); the simulator does not host it.
## 4. Division [D]
- **Stage 1 keeps** what reinstalls everything: cold entry, link, default boot, key snapshot, the simple menu, USB service and protocol v1, the file engine and file commands, stage-1 install and uninstall (012), the stage-2 loader with trial and fallback, the on-device confirmation prompt (it guards stage-1 commands), the in-menu uninstall row, `otp_keys`, every stock call. Its own additions are ~20 KB against 24.2 + 9.7 [I].
- **Stage 2 gets:** tfw.check (first payload), MTP, strict package reader and job runner, `.tfw` install, content switch and packs, settings page, full menu (machines, mods page), mod linker, patcher and assembly, companions.
- **The loop stays in stage 1.** Stage 2 exports a table (draw, key, command handlers, job slice); the home loop calls it each turn through one audited trampoline (`xyf_stage2_call`, target checked inside the window), so USB, keepalives and guard reloads never wait on stage 2.
- **Stock calls: none from stage 2.** It calls an append-only services table of stage-1 wrappers, so allowlist, CRC spans and scoped groups stay in stage 1; the allowlisted MTP driver calls become wrappers (~1 KB). Builder: `check_stage2_image` plus #13's data-word scan (no stock, ROM, MMR or stage-1 address in `.data`/`.rodata`), `test_xyf_freestanding`, section GC.
- **A needs a part** (assembly, content switch, `.tfw` hash). One image for both roles, like stage 1: A reads the file into its own window (same address, own SDRAM) when B starts a job that needs it; B gets it over the image transfer; B checks the SHA-256 A reports. An A reset mid-job returns A to stage 1 (016).
## 5. Update and recovery [D]
- **`xyf-stage2`:** file into `tmp/`, A `accept`s it, kept as `stage2/<version>-<sha8>.xys2`; the commit is one proven slot write (`stage2` new, `stage2_previous` old, `stage2_tries=0`), no rename (unproven [U 009]). Replaces 010's `{main,previous}.xys2`.
- **Try once:** for an unconfirmed stage 2, stage 1 writes `stage2_tries+1` before the call (menu or USB entry only); stage 2 confirms through a service after its first frame and key poll; after 2 failures stage 1 loads `stage2_previous` and says so. A flag beside the reboot marker (`0x8bfff000`) marks "in stage 2": after a guard reset stage 1 skips stage 2 with no write. Escape: PLAY plus one more key at power-on gives the stage-1 menu.
- **The default never depends on stage 2:** a plain power-on never reads it [C today; kept]; a broken stage 2 leaves the stage-1 menu with USB.
- **`xyf-stage1`:** the INSTALL path (writer on B, later A) with today's checks; `stage1/previous.ldr` kept so the menu can reinstall it. The reader always prefers partition 1 [C DESIGN], so a stage 1 that cannot boot needs the dev menu (no external hardware while `bootloader.dev` exists): stage-1 changes stay rare and qualified. **Versions:** `this_stage1` is the services version; `min_stage1` above it is refused (built: `Check::stage1`) with "needs a newer XY Forever"; services only grow, so stage 1 is installed first, then stage 2.
## 6. Boot time
Plain power-on: unchanged [C]; only stage-1 growth costs (~0.27 ms/KB [I]). Menu or USB entry with a 100-200 KB stage 2: A read 6-40 ms, A SHA-256 8-17 ms, transfer 55-110 ms (≥ 1.8 MB/s), B SHA-256 8-17 ms, copy < 10 ms [I from 019's M]: **+0.1-0.2 s** on the ~2 s to home (not re-measured). A's part costs nothing until a job needs it.
## 7. Order and split of stage 2
Stage 2 must exist before MTP and #24, and before #11 and #12 in practice. Built as stage 2 from the start: MTP, #11, #12, #14, #18, #20, #21's page, the menu's machines, #24 and the Mods page, #31, #33 (the linker and patcher are host-tested libraries, unaffected). New needs: MTP, #11, #14 and #24 on the install step below.
| Step | Done when |
|---|---|
| build: stage 2 in the product | loader in the v3 home path on B, on demand on A; services ABI 3, `xyf_stage2_call`, loop in stage 1; link loss takes the lone-restart path (016); tfw.check moved (sizes reported); builder audits of §4; narrow offline modes removed; simulator hosts stage 2 (missing, corrupt, `min_stage1`, hang, link loss); test: plain boot reads no stage 2 |
| build: install, trial, rollback | slot keys, tries, confirm, revert, SDRAM flag, escape key, stage 2 in `state.get`/HELLO; simulator: a cut at every step; a hanging stage 2 reverts |
| build: menu uninstall, stage-1 self-update | menu row + device confirmation; `xyf-stage1` from a file, previous kept |
| review | stage 2 reaches no stock code; plain boot untouched; every failure ends in the stage-1 menu with USB |
| device, safe | loader build, no stage-2 file: plain and PLAY boots as before; then a stage 2: menu and tfw.check work |
| device, risky | hanging stage 2: guard reset, revert, USB stays; at most one power cycle |
| device, risky | stage-1 self-update from the menu, dev-menu kit ready; menu uninstall last (reinstall through the dev menu) |

**Stock calls:** none new [C/I]. **Open questions:** the escape key; 2 or 3 tries; keep `stage1/previous.ldr` (~90 KB)?; a 32 KiB stage-1 reserve now or only if needed?
