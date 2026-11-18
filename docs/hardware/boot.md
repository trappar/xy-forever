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
| USB identities | stock OS: `2367:0021`, vendor class 0xFF (another mode `2367:8021`, class 01); XY Forever composite: `2367:0021`, device class EF/02/01, CDC plus MTP interface 2; stock CDC fallback: "OP_XY Boot", classes 02 + 0A; [transport](../protocol/transport.md) |
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
  `0x8b000000` exists in offline modes only (none in the product). Memory map below.
- **Reboot marker:** SDRAM `0x8bfff000` ("YXFR"; magic, ~magic, reason, crash
  restarts). SDRAM survives a soft reset, not a power cycle.
- **WDOG0** at `0x20046000`: the stock reset `11a03518` arms it with a short count. XY Forever
  does not use it (its watchdog guard was removed in protocol 1.14: it never recovered a hang).
- **Timing:** read the current boot's timing fields, not old stopwatch estimates. A full yaffs scan
  can take tens of seconds; a checkpoint mount is much faster. The processors have different time origins.

## Memory map (product)

Same on A and B (separate SDRAM). Evidence for the free gap and the hand-off: [architecture/boot.md](../architecture/boot.md#memory-map-product).

| Range | Owner, use | Evidence |
|---|---|---|
| `0x88000000..0x88a63bf8` | stock bootloader image, data, BSS | static segments (`check_boot_reservations`) |
| `0x88a65bf8..0x8aa65bf4` | stock allocator arena (yaffs device, caches) | allocator table `0x88a2a4d0` (build-checked) |
| `0x8a000000..0x8a800000` (+64 KiB margin) | stock stream buffer: the boot loader reads `os.ldr` here (8 MiB cap, `88a34a12`); our boot copy (staging -> here, <= `staging_bytes` 0x7e0000 B) for the RAM loader `88a3477a` | listing; `cold.cpp` protected |
| `0x8ab00000..0x8ab80000` | XY Forever code + data (512 KiB window) | linker `ASSERT`, `verify_elf` |
| `0x8ab80000..0x8ab84000` | XY Forever stack (16 KiB; entry sets SP `0x8ab84000`) | entry audit (P0.H/P0.L) |
| `0x8ab90000..0x8ac00000` | stage-2 window (dormant loader, B only) | `check_stage2_image` |
| `0x8ac00000..0x8b000000` | unused | |
| `0x8b000000..0x8b800000` | B's mod job: 64 KiB state, then the work region (6.3 MiB peak); MTP session state (33 KiB, sim only) | `cold_layout.hpp`, `check_memory_map` |
| `0x8b800000..0x8c000000` | our scratch, catalog, USB frames, staging (B 7 MiB image buffer), reboot marker `0x8bfff000` | |
| `0x8c000000..0x8c03c000` | B's framebuffer | |
| `0x8d3be380..0x8d4d0380` | census, reporter, carry slot (inside the OS image's heap) | |
