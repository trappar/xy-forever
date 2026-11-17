# 022: Why a lone reset of B never completes (detail)

Detail of [022-lone-b-reset.md](022-lone-b-reset.md).

## 3. Recovery, in principle only (not to be built)

- (a) B hangs while A runs XY Forever: A would have to reset after B does, with A's silence bound longer than B's guard (108). This needs no new stock call. (b)
  A restarts alone: B must not reset. It could release A's bootloader with op 7 (stock `889f7b56`, **not** on the allowlist, `xyf_build.py` 131), then redo the
  link start. A hung A needs A's own watchdog. Confidence: moderate.

## 5. Device evidence: no experiment that risks a freeze

- Read-only, if PEEK's ranges allow it (reads only, no stock call):
  - B's RCU0_STAT `0x20000004` (BMODE field and reset cause), SVECT0 `0x20000020` (expected `0x11a00000`) and BCODE `0x2000001c`.
  - B's SPIBOOT record (pointer at `0x88a20274`: found flag and timestamp), read after a power-on and after one routine REBOOT. "Not found after REBOOT"
    confirms the warm re-entry.
  - B's PORTA_DATA `0x2004000c` bit 6 while A runs XY Forever.
- None of these reproduces the hang. Nothing else is needed for the recommendation in §4.

## 6. What the simulator must model

1. B's `11a03518` always lands in B's bootloader, but B's boot-A step (before the display reset, USB and the link) blocks until A resets at most about 0.1 s
   before it or at any time after it.
2. A's `11a03518` re-enters A's bootloader by itself; it serves B's boot reads and waits for op 7 with no bound.
3. The screen keeps its last frame until B passes the boot-A step. A lone B reset with A up (bootloader or XY Forever) is a permanent freeze.
