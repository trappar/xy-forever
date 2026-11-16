# 022: Why a lone reset of B never completes (plan item 0.9j)

Research record, 2026-11-14; no device action. Listing: the private `analysis/bootloader-121-listing` (`linear121.ann.asm`; one image, run by both processors). Evidence: device passes 1, 5 and 8 (the maintainer's, not shared), HISTORY, commit messages. Tags: **[L]** read from the listing, **[C]** our code, **[M]** measured, **[I]** inferred, **[U]** unknown. Supersedes record 021's causes; 021 also misnamed `0x2000001c`: it is RCU0_BCODE, not SIDIS [L, Ghidra pspec].

## 1. How B boots, and what its reset needs

- **B boots A, not the reverse.** In main (`88a35668`), `88a35b90` splits on role. Role 1 (B) waits 10 ms, makes PB10 an input (`88a305e4(1,0x400,0)`), then calls `88a1d34c` ("bfs not responding") [L]. `88a1d34c` sets up SPI2 (`0x20057000`, B's SPI-NOR: status op 5, read op 3) and SPI0 (`0x20055000`, select 3) (`88a1cf74`). It reads the slot byte at flash `0xd000`, which picks `0x10000` or `0x80000`, and waits up to 1M polls for B's PA6 to go low ("bfs not responding" on timeout). Then it sends `0x03` to A [L].
  - Reply `0xff`: B streams the bootloader's LDR blocks (signature `0xad`, final flag bit 15) from its flash into A (`88a1d23e`, `88a1d1e4`). Before every byte it waits for PA6 low, **with no bound** [L].
  - Any other reply ("A already running"): B sends eleven `0x0f` bytes, then waits for PA6 high, **with no bound** (`88a1d19e`) [L].
  - Then B opens the bridge master (`889f5bfc`) [L]. The running link uses the same wires: B's SPI0 master (`889f54c8`) to A's SPI2 slave (`889f62fa`) [L].
- So B loads its own code from its own SPI flash. SPIBOOT (flash 0, at most `0xc000`) leaves a marker in B's DMA9 registers (`88a1d9c2`) [L], and A is SPI-slave-booted by B [I, high]. Which pin on A drives B's PA6 is [U] (A's ROM HWAIT is the likely candidate). A's bridge slave drives its own PA4 and PB2 [L].
- **B's display is reset only after that step.** `88a1d9c2` -> `889ef16c` (boe_239 "pt1" panel reset) runs in main at `88a35714`, after `88a35b90` returns [L]. A blank screen therefore means B got past its boot-A step. A screen that keeps its frame means B stopped at or before it [I, high].
- **A at its own start** (role 0, `88a35b90`): eMMC init (`88a3434e`), bridge slave open (`889f6558`, `889f6686`), then a loop with no bound until op 7 (`889f665e`) or B's POWER_OFF flag `0x88a1cdc1`. On op 7: teardown `889f5d72`, then A reads `os.ldr` itself (`88a34a00`). On POWER_OFF: message `0x32`, 30 ms, `11a03518` [L]. Later, A does nothing for B.
- **`11a03518`**: CLI; RCU0_MSG_CLR = all ones; RCU0_BCODE bit 4; mailbox `11a03318`; RCU0_STAT = `0xf`; BCODE cleared, then bit 4 set again; RCU0_CTL bit 8; SEC on, source 3 `SCTL` 6, `FCTL` `0x41`; WDOG0 `0x1312d00`, enable; spin [L]. A watchdog fault becomes a system reset. Nothing in it depends on A or on a peripheral [L].
  - The bootloader sets SVECT0 = `0x11a00000` at start (`11a00024`). On re-entry, `11a0141a` re-scrubs L1 instead of reloading it, gated on RCU0_MSG bit 16, which the reset clears [L]. So a soft reset re-enters the resident bootloader on each processor, with no SPI boot [I, about 70%; the meaning of BCODE bit 4 is U]. This is the only model that fits both 108 and 160/162 (§2).
- **The vendor never resets B alone.** The dev-menu reset (`88a1f34e` -> `88a1ddc4`) first asks A to power off and waits ("request bfs to power off", "bfs shutdown complete", `88a24fb2`). A resets itself (`88a35c50`), and only then does B call `11a03518` [L].

## 2. Each case

- **Rule [M, 9 cases]:** a lone B reset completes only if A resets at most about 0.1 s before it, or at any time after it. Works: REBOOT (A about 0.1 s first, dozens of times), 041 (A reset after its session failed), 108 (A 25 s after). Freezes: A never reset (096-107, 132-134, 148-150, 153); A reset long before (135: about 9 s; 160 and 162: 1.4 s). The boundary lies between 0.15 s and 1.4 s [U].
- **Joint REBOOT:** A answers and resets; B stops USB 100 ms later and resets [C]. At B's boot-A step, A has just restarted and its slave is not yet open, so B gets past it. The screen blanks (`889ef16c`), A's bootloader serves B's `os.ldr` reads and op 7, and both are back in about 2 s. This matches the screen seen blank for a split second [M/I].
- **Lone B reset while A runs XY Forever (153):** A's slave is live, so B takes a branch that waits with no bound for PA6, or, if it gets past, it meets A's XY Forever, which refuses the select (016) [I]. A never resets. USB stays off and the last frame stays on screen [I]. The 153 screen was not reported [U].
- **Lone B reset while A sits in its stock bootloader (160, 162):** A had restarted 1.4 s earlier. Its bootloader is up, with its slave open, waiting for op 7. B's boot-A step meets a running A and waits on PA6 forever. The screen never blanks and the menu frame stays, as seen both times [M]. Closing the link first (162) could not help: the hang lies in B's next boot, not in its reset [I, high]. That refutes 021's (a) and 021's fix.
- **108's recovery:** B waited in its bootloader. A's reset 25 s later (`a_alone_ms`) let B through: either B's PA6 wait or B's in-flight select. A's fresh bootloader then served B, and both came back [M/I].
- Side finding: 104-107 ("a reset with USB running freezes B") were lone B resets too. 107 stopped USB and still froze; 108 stopped USB, A followed, and it worked. Stopping USB first is harmless but was never shown to be needed [I].

## 3. Recovery, in principle only (not to be built)

- (a) B hangs while A runs XY Forever: A would have to reset after B does, with A's silence bound longer than B's guard (108). This needs no new stock call. (b) A restarts alone: B must not reset. It could release A's bootloader with op 7 (stock `889f7b56`, **not** on the allowlist, `xyf_build.py` 131), then redo the link start. A hung A needs A's own watchdog. Confidence: moderate.

## 4. Recommendation: the smallest safe product state

Principle: XY Forever never resets B unless A has acknowledged a REBOOT item in that same exchange. A lost link ends in a live menu or a clear halt screen, never in a restart attempt by our code.
- **Remove F2** (`usb_service::link_lost_restart`, called at `key_ui.cpp` 436): it is guaranteed to freeze after a lone A restart [M].
- **Remove the B-side guard reset** (`guard_expired` -> `usb_stop` -> `11a03518`), the GUARD command and A's guard (`a_guard`, `a_guarded`). Every guard reset is a lone B reset, which freezes unless A happens to follow.
- **Remove A's idle follow** (`file_boot.cpp` 734-738) **and A's alone rule** (`a_alone_ms`, 505-510). A lone A restart is what produces the 160 freeze. On its idle window A goes back to the pre-159 behaviour (halt, or keep waiting).
- **Remove the probe** (`probe_ms`, every fourth keepalive). It existed only to trigger F2; plain keepalives are enough to detect a lost link.
- **Remove `diag.restart_a`** (`restart_a_item`, `file_boot.cpp` 536). Its only use was testing F2.
- **Keep:** keepalives and `b_lost`. On `b_lost` at home, B draws "Internal link lost. Turn the OP-XY off and on." and leaves USB up, answering STATUS/HELLO and refusing everything else with `link` (USB off, as before 159, is acceptable too). REBOOT and restart are refused unless A's ack arrives; with no ack, B does not reset. Keep A's F1 (hold a restarted B's boot requests unanswered, then restart A). It only acts after B has reset, which is the safe order, and it never fires once nothing resets B alone. Keep USB stop before every reset.
- Update HARDWARE (lines 112-113 and "Restart order") with §1. Its "link request open" claim is wrong.

## 5. Device evidence: no experiment that risks a freeze

- Read-only, if PEEK's ranges allow it (reads only, no stock call):
  - B's RCU0_STAT `0x20000004` (BMODE field and reset cause), SVECT0 `0x20000020` (expected `0x11a00000`) and BCODE `0x2000001c`.
  - B's SPIBOOT record (pointer at `0x88a20274`: found flag and timestamp), read after a power-on and after one routine REBOOT. "Not found after REBOOT" confirms the warm re-entry.
  - B's PORTA_DATA `0x2004000c` bit 6 while A runs XY Forever.
- None of these reproduces the hang. Nothing else is needed for the recommendation in §4.

## 6. What the simulator must model

1. B's `11a03518` always lands in B's bootloader, but B's boot-A step (before the display reset, USB and the link) blocks until A resets at most about 0.1 s before it or at any time after it.
2. A's `11a03518` re-enters A's bootloader by itself; it serves B's boot reads and waits for op 7 with no bound.
3. The screen keeps its last frame until B passes the boot-A step. A lone B reset with A up (bootloader or XY Forever) is a permanent freeze.
