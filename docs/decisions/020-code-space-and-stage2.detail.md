# 020: Code space and stage 2 (detail)

Detail of [020-code-space-and-stage2.md](020-code-space-and-stage2.md).

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
- **Built [C]:** header `XYS2`, 96 B (abi, load and entry, size, CRC-32, SHA-256, `min_stage1`, version, build id, header CRC; `validate`/`accept`/`classify`);
  packer `xy_forever/xyf_stage2.py`; images `stage2_hello.cpp` (ABI 1) and `stage2_menu.cpp` (ABI 2: returns requests, stage 1 performs boot once and set
  default); trampoline `xyf_stage2_enter.s`, the one register jump the call audit admits; `check_stage2_image` (no BSS, entry at the window, no call or
  high-half address outside the image); host tests `test_xyf_stage2*.py`.
- **Load [C `file_boot.cpp`]:** only with PLAY, after the catalog. B sends item `0x12`; A reads `/yaffs2/xy-forever/stage2/main.xys2` read-only into staging,
  `accept`s it (with SHA-256), serves it or an 8-byte `XYSN` refusal; B (15 s bound) re-runs `classify`, copies to `0x8ab20000`, re-checks the CRC,
  FLUSH/IFLUSH, calls it on its stack with 4 services (frame begin/end, next event, ms); it calls no stock code. Missing or corrupt: the stage-1 menu. Link
  lost: "boot built-in", gone since 088.
- **Only named:** state keys `stage2`, `stage2_previous`, `stage2_tries` (STORAGE, DESIGN; `xyf_state` has none), the tries fallback, `previous.xys2`, an A
  part, the `xyf-stage2` install, crash handling (a hang today meets only the guard, 016). All code sits in the pre-v3 branch (not `XYF_ONE_SESSION`) of
  `--xyf-stage2(-menu)`, 48 KiB layout; the product has none. **Never ran on the device** (HISTORY, FINDINGS-stage2); the simulator does not host it.

## 6. Boot time
Plain power-on: unchanged [C]; only stage-1 growth costs (~0.27 ms/KB [I]). Menu or USB entry with a 100-200 KB stage 2: A read 6-40 ms, A SHA-256 8-17 ms,
transfer 55-110 ms (≥ 1.8 MB/s), B SHA-256 8-17 ms, copy < 10 ms [I from 019's M]: **+0.1-0.2 s** on the ~2 s to home (not re-measured). A's part costs nothing
until a job needs it.

## 7. Order and split of stage 2
Stage 2 must exist before MTP and #24, and before #11 and #12 in practice. Built as stage 2 from the start: MTP, #11, #12, #14, #18, #20, #21's page, the menu's
machines, #24 and the Mods page, #31, #33 (the linker and patcher are host-tested libraries, unaffected). New needs: MTP, #11, #14 and #24 on the install step
below.
| Step | Done when |
|---|---|
| build: stage 2 in the product | loader in the v3 home path on B, on demand on A; services ABI 3, `xyf_stage2_call`, loop in stage 1; link loss takes the lone-restart path (016); tfw.check moved (sizes reported); builder audits of §4; narrow offline modes removed; simulator hosts stage 2 (missing, corrupt, `min_stage1`, hang, link loss); test: plain boot reads no stage 2 |
| build: install, trial, rollback | slot keys, tries, confirm, revert, SDRAM flag, escape key, stage 2 in `state.get`/HELLO; simulator: a cut at every step; a hanging stage 2 reverts |
| build: menu uninstall, stage-1 self-update | menu row + device confirmation; `xyf-stage1` from a file, previous kept |
| review | stage 2 reaches no stock code; plain boot untouched; every failure ends in the stage-1 menu with USB |
| device, safe | loader build, no stage-2 file: plain and PLAY boots as before; then a stage 2: menu and tfw.check work |
| device, risky | hanging stage 2: guard reset, revert, USB stays; at most one power cycle |
| device, risky | stage-1 self-update from the menu, dev-menu kit ready; menu uninstall last (reinstall through the dev menu) |

**Stock calls:** none new [C/I]. **Open questions:** the escape key; 2 or 3 tries; keep `stage1/previous.ldr` (~90 KB)?; a 32 KiB stage-1 reserve now or only if
needed?
