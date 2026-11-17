# 020: Code space and stage 2

Research record, 2026-11-14: build 158's `module.map` and `manifest.json`, objects compiled with the product flags in a temporary worktree (removed), an
uncommitted linker snapshot of `native/mod_*`, an earlier update study (the maintainer's, not shared). No device. **[C]** read from code, map or listing,
**[M]** measured, **[I]** inferred, **[U]** unknown, **[D]** proposal.

**In brief.** The 24,248 B is a builder constant, not a hardware limit. The planned features need ~105-140 KB, five times that. Stage 1 runs out at MTP or at
the package jobs (#11, #12), whichever lands first after M2. Mod assembly cannot fit: the unfinished linker alone is 29.6 KB. Stage 2 is built (header, loader,
ABI 1/2), but only in two obsolete offline modes, on B only, never run on the device, with no tries or rollback. Recommendation: every feature after the file
engine goes into stage 2, on both processors; stage 1 owns the loop and every stock call; build the stage-2 steps (section 7) right after the lone-restart fix
(016), before MTP and #11. No new stock call.
## 1. Stage-1 limits (A and B run the same image: every bound and every byte counts on both [C])
| Bound | Value | Binding |
|---|---|---|
| Builder `layout()` (wide): code `0x8ab00000..0x8ab1c000` = 114,688 B, 16 KiB stack to `0x8ab20000`; linker `ASSERT(__end <= limit)`, `verify_elf` [C] | 158: text 74,088 + rodata 11,265 + data 5,064 + pad = 90,440 B, **24,248 B free** [M] | yes, the only one |
| Boot partition (capacity read at install, `889f65f0`) [C] | 3.4-3.5 MB images installed and booted [C HARDWARE, 012]; exact size [U] | no (outer `os.ldr` 90,512 B) |
| Real bootloader: stream at `0x8a000000..0x8a800000`, records inside `ALLOWED` [C `outer.py`, `cold.cpp`] | 8 MiB | no |
| Above the stack: stage-2 window `0x8ab20000..0x8ac00000` (896 KiB), `0x8ac00000..0x8b000000` unreferenced (4 MiB) [C 019]; no static stock segment (`check_boot_reservations`) [C]; stock use at cold time never tested by a write [U] | 4.9 MiB | no |
| Cold LDR policy protects `0x8ab00000..XYF_STACK_END` from library images; the window is not protected [C `cold.cpp`]; the OS loads elsewhere [M 019] | | no |

**Raising it** is cheap in code: `WIDE_STACK(_END)`, the window in `xyf_stage2.hpp`/`.py`, `tfw_extract.py`, `sim.py`, layout tests; the entry stack and
protected range follow `XYF_STACK_END` [C]. Risks: SDRAM never loaded at power-on (as the 043 widening was) [U]; each stage-1 KB costs ~0.27 ms on every boot (B
enters at 1.07 s with 77 KB, 1.98 s with 3.5 MB [C HARDWARE]; linear [I]); every change is a boot-partition write with no embedded fallback since 088. A reserve
lever (+32 KiB), not the plan.
**Top consumers [M map]:** usb_service 14,306; peer_services 9,688; file_boot 8,852; storage_server 7,365; cold_storage 5,879; tfw.check group 8,989 (p224
3,468, tfw_check 1,984, tfw 1,960, aes 1,052, otp_keys 312, tfw_signature 213); menu and UI 6,700; library and state 5,725; file_client 2,624; peer_boot 2,600;
diagnostics (census, trace_slot*, warm_snapshot, record) 2,582. **Reclaimable without losing function [I]:** tfw.check into stage 2 (8,677 B; `otp_keys` stays
as a service) and the second CRC-32 table (`inflate.o` 1,024 B rodata beside `xyf_crc32`'s, used by peer_boot): ~9.7 KB. Diagnostics only by dropping the cold
report.

Detail: §2, §3, §6, §7 in [020-code-space-and-stage2.detail.md](020-code-space-and-stage2.detail.md)

## 4. Division [D]
- **Stage 1 keeps** what reinstalls everything: cold entry, link, default boot, key snapshot, the simple menu, USB service and protocol v1, the file engine and
  file commands, stage-1 install and uninstall (012), the stage-2 loader with trial and fallback, the on-device confirmation prompt (it guards stage-1
  commands), the in-menu uninstall row, `otp_keys`, every stock call. Its own additions are ~20 KB against 24.2 + 9.7 [I].
- **Stage 2 gets:** tfw.check (first payload), MTP, strict package reader and job runner, `.tfw` install, content switch and packs, settings page, full menu
  (machines, mods page), mod linker, patcher and assembly, companions.
- **The loop stays in stage 1.** Stage 2 exports a table (draw, key, command handlers, job slice); the home loop calls it each turn through one audited
  trampoline (`xyf_stage2_call`, target checked inside the window), so USB, keepalives and guard reloads never wait on stage 2.
- **Stock calls: none from stage 2.** It calls an append-only services table of stage-1 wrappers, so allowlist, CRC spans and scoped groups stay in stage 1; the
  allowlisted MTP driver calls become wrappers (~1 KB). Builder: `check_stage2_image` plus #13's data-word scan (no stock, ROM, MMR or stage-1 address in
  `.data`/`.rodata`), `test_xyf_freestanding`, section GC.
- **A needs a part** (assembly, content switch, `.tfw` hash). One image for both roles, like stage 1: A reads the file into its own window (same address, own
  SDRAM) when B starts a job that needs it; B gets it over the image transfer; B checks the SHA-256 A reports. An A reset mid-job returns A to stage 1 (016).
## 5. Update and recovery [D]
- **`xyf-stage2`:** file into `tmp/`, A `accept`s it, kept as `stage2/<version>-<sha8>.xys2`; the commit is one proven slot write (`stage2` new,
  `stage2_previous` old, `stage2_tries=0`), no rename (unproven [U 009]). Replaces 010's `{main,previous}.xys2`.
- **Try once:** for an unconfirmed stage 2, stage 1 writes `stage2_tries+1` before the call (menu or USB entry only); stage 2 confirms through a service after
  its first frame and key poll; after 2 failures stage 1 loads `stage2_previous` and says so. A flag beside the reboot marker (`0x8bfff000`) marks "in stage 2":
  after a guard reset stage 1 skips stage 2 with no write. Escape: PLAY plus one more key at power-on gives the stage-1 menu.
- **The default never depends on stage 2:** a plain power-on never reads it [C today; kept]; a broken stage 2 leaves the stage-1 menu with USB.
- **`xyf-stage1`:** the INSTALL path (writer on B, later A) with today's checks; `stage1/previous.ldr` kept so the menu can reinstall it. The reader always
  prefers partition 1 [C DESIGN], so a stage 1 that cannot boot needs the dev menu (no external hardware while `bootloader.dev` exists): stage-1 changes stay
  rare and qualified. **Versions:** `this_stage1` is the services version; `min_stage1` above it is refused (built: `Check::stage1`) with "needs a newer XY
  Forever"; services only grow, so stage 1 is installed first, then stage 2.
