# Hardware and stock-code facts: boot and memory

Part of [Hardware and stock-code facts](../HARDWARE.md).

## Versions and identities

| Item | Value |
|---|---|
| Real bootloader (SPI) | 1000.0.20+0 (1.2.1), SHA-256 `139459f2…f75d`, `STOCK_121/bootloader.ldr` (`xy_forever/paths.py`) |
| 1.2.5 bootloader (never install) | 1000.0.23+0 |
| Stock 1.2.1 `os.ldr` | SHA-256 `60358afd…2b52f`, 2,373,336 B, `STOCK_121/os.ldr` |
| 1.2.5 `os.ldr` | SHA-256 `bed98a00…4283` |
| 1.2.1 → 1.2.5 companions | backplane 1.0.7 → 1.0.8, BLE 1.0.1 → 1.0.2; factory content identical |
| Normal-OS USB (MTP) | VID `0x2367`, PID `0x0021` ("OP-XY"), vendor class 0xFF; another mode PID `0x8021`, class 01. XY Forever's service: same ids, "OP_XY Boot", CDC classes 02 + 0A (session logs; protocol `transport`) before product build 188, and from 188 when a boot-time check fails; otherwise, from 188, one composite `1209:0001` (pid.codes test pair, temporary): CDC serial plus MTP on interface 2 |
| SDRAM | 256 MiB (DMC_CFG `0x522`) |
| CCLK | 400 MHz (word `0x88a2ef28` = 400000000, written by `88a30fbc`); ms = cycles `*21 >> 13` |

## Boot and processors

- **How stock boots:** B reads `os.ldr` from A over SPI and sends op 7 to release
  A. Then each processor boots its own copy (both run `os.ldr`).
- **Role byte:** `0x8807cac4`: A = 0 (PORTA_DATA `0x12`), B = 1 (PORTA_DATA
  `0x6160`). The PA2 role strap reads 0 at cold time, because stock `88a1d510`
  enables PA0/2/8 inputs only while it samples them.
- **Interrupts:** the GPIO callback vector is at `0x11b00000` (L1 data scratch), not
  SDRAM. IMASK at cold entry is `0x881f`.
- **Retained heap:** `0x88a65bf8..0x8aa65bf4` (allocator table at `0x88a2a4d0`). The
  old default module address `0x8a800000` lay inside it.
- **Cold layout:** ROM trampoline at `0x11a00000`, RAM loader `0x88a3477a`; the embedded OS at
  `0x8b000000` exists in offline modes only (none in the product since 088). Memory map below.
- **Reboot marker:** SDRAM `0x8bfff000` ("YXFR"; magic, ~magic, reason, crash
  restarts). SDRAM survives a soft reset, not a power cycle.
- **Watchdog guard:** WDOG0 at `0x20046000`, used the way the stock reset `11a03518`
  uses it.
- **Timing baselines (stopwatch, switch → OS visible):** stock
  1.2.1 ~4.6 s; XY Forever 087 ~7.15 s; 088 ~5.84 s; 090 estimated ~4.5 s. With
  the 77 KB product `os.ldr`, B enters XY Forever 1.07 s after reset and A is ready
  17 ms later (with the 3.5 MB file: 1.98 s and 0.55 s). Older notes:
  B enters about 0.53 s before A (Test 023). Library boot
  reached the OS in about 13 s, product normal boot in 6 s, official 1.2.1 in 11 s
  (Test 052). A stock boot-time baseline was never measured.

## Memory map (product, build 171)

Same on A and B (separate SDRAM). Evidence for the free gap and the hand-off: [architecture/boot.md](../architecture/boot.md#memory-map-product-build-171).

| Range | Owner, use | Evidence |
|---|---|---|
| `0x88000000..0x88a63bf8` | stock bootloader image, data, BSS | static segments (`check_boot_reservations`) |
| `0x88a65bf8..0x8aa65bf4` | stock allocator arena (yaffs device, caches) | allocator table `0x88a2a4d0` (build-checked) |
| `0x8a000000..0x8a800000` (+64 KiB margin) | stock stream buffer: the boot loader reads `os.ldr` here (8 MiB cap, `88a34a12`); our boot copy (staging -> here, <= `staging_bytes` 0x7e0000 B) for the RAM loader `88a3477a` | listing; `cold.cpp` protected |
| `0x8ab00000..0x8ab80000` | XY Forever code + data (512 KiB window; 172 uses ~166 KB) | linker `ASSERT`, `verify_elf` |
| `0x8ab80000..0x8ab84000` | XY Forever stack (16 KiB; entry sets SP `0x8ab84000`) | entry audit (P0.H/P0.L) |
| `0x8ab90000..0x8ac00000` | stage-2 window (dormant loader, B only) | `check_stage2_image` |
| `0x8ac00000..0x8b000000` | unused | |
| `0x8b000000..0x8b800000` | B's mod job: 64 KiB state, then the work region (6.3 MiB peak); MTP session state (33 KiB, sim only) | `cold_layout.hpp`, `check_memory_map` |
| `0x8b800000..0x8c000000` | our scratch, catalog, USB frames, staging (B 7 MiB image buffer), reboot marker `0x8bfff000` | |
| `0x8c000000..0x8c03c000` | B's framebuffer | |
| `0x8d3be380..0x8d4d0380` | census, reporter, carry slot (inside the OS image's heap) | |
