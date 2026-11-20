# XY Forever architecture: boot

Part of [XY Forever architecture](../ARCHITECTURE.md).

## Boot chain

```
power on -> real 1.2.1 bootloader (SPI flash, never modified)
         -> loads os.ldr from the NAND boot partition = XY Forever
         -> XY Forever "cold" code runs on both processors before any OS
         -> boots the chosen firmware file from /yaffs2/xy-forever/firmware/<id>/
            (no embedded OS: a default that can't boot opens the menu;
             a boot that loads no image shows why and halts until a power cycle)
```

The real bootloader still handles COM at power-on (dev menu), so it stays the
last-resort recovery.

The product's outer `os.ldr` (built by `xyf_build.py`, packed by `xy_forever/outer.py`) has two load records: a ROM trampoline at `0x11a00000` and the
cold module at `0x8ab00000` (code window to `0x8ab80000`, 16 KiB stack to `0x8ab84000`; see "Memory map" below). A cold descriptor carries the CRCs of the stock
helpers the module calls (payload size 0). The other, offline modes still embed the stock OS payload at `0x8b000000` as a third record (`NO_EMBEDDED_MODES`).

## Memory map (product)

Table and owners: [hardware/boot.md](../hardware/boot.md#memory-map-product). The module (code window `0x8ab00000..0x8ab80000`), its stack
(`..0x8ab84000`) and the dormant stage-2 window (`0x8ab90000..0x8ac00000`) lie in `0x8aa65bf4..0x8b000000`, above the stock allocator arena and below B's mod
job region; `xyf_build.py` `check_memory_map` (build and `--verify`) fails the build if either layout leaves that gap or touches the boot source, job region,
staging, framebuffer or report slots.

The offline stage-2 demos use `0x8ab00000..0x8ab0e000` for the cold module and
`0x8ab0e000..0x8ab12000` for its 16 KiB stack. They share the product's memory map audit.

- **Free at power-on:** outside every static bootloader segment and the arena (both build-checked); the 1.2.1 bootloader's disassembly loads no immediate in
  `0x8a000000..0x8c000000` but its stream buffer `0x8a000000`, and its initialized data holds no aligned pointer into `0x8ab00000..0x8ac00000` (one byte-table
  word at `0x1190029c` looks like one); its load path reads at most 8 MiB into `0x8a000000` (`88a34a12`) and then places our two records. Our own buffers are
  all elsewhere; peer DMA (`owned_buffer_range`) refuses `0x8ab00000..XYF_STACK_END`. The bounds are enforced offline;
  byte checks alone do not qualify a new cold-time memory use on the device.
- **Hand-off:** the image is copied from staging to `0x8a000000` (at most `staging_bytes` 0x7e0000 B, so below `0x8a800000`); every record is admitted by
  `cold.cpp`'s policy, whose protected range `0x8ab00000..XYF_STACK_END` covers the running code and stack, before the stock RAM loader (also protected) places
  it. Stock 1.2.1 (2,373,336 B), 1.2.5's `os.ldr` and a device-assembled mod image (2,493,876 B) place nothing in the gap
  (`test_official_images_pass_the_product_policy`); any image up to the 8 MiB boot source meets the same check, so size does not matter.

## Processors

| | Processor A (role 0) | Processor B (role 1) |
|---|---|---|
| Owns | NAND storage (yaffs) | screen, keys (PSoC), USB |
| Enters XY Forever | about 0.5 s after B | first |
| Link role | SPI slave (server) | SPI master (client) |

A decides nothing on its own: it serves B and boots what both agreed. B makes
every user-facing decision.

## Boot flows (product)

- **Plain power-on:** file-boot session → A checks the default library entry
  (`info.txt` size/CRC, LDR policy) → serves it → both boot it. No USB, never waits
  for a computer. A default that can't boot opens the menu with the reason.
- **PLAY at power-on:** the boot menu with USB on.
- **USB service:** entered after XY Forever's own reboots (reboot marker at SDRAM
  `0x8bfff000`); host commands in [operating/host-tool.md](../operating/host-tool.md).

## Boot model v3 (proven)

| Situation | Behaviour |
|---|---|
| Plain power-on | straight to the default OS, as fast as possible; no USB |
| Default can't boot | boot menu (with the reason) |
| Power-on with PLAY | boot menu with USB on |
| Agent reboots | to the OS or to the menu only (held, USB-ready); there is no watchdog guard |
| Agent attached to the menu | menu and USB service are one: every command works from the menu, never a reboot to switch (a transient "working" screen is fine) |
| Menu timeout | never |

Implementation: **one link session per boot** (`XYF_ONE_SESSION`). A keeps one fresh read-only storage
session for the whole link session and unmounts only right before an image is frozen for the boot (a
second `begin_fresh` in one boot is `busy`: the constructed yaffs device stays in the stock device
list). While nothing is frozen, B's file requests are routed to the storage server; while a transfer is
frozen, the image protocol runs. Routing is by "frozen?", never by op number (0x18/0x11/0x12 are also
install ops). On B the menu is the home loop: screen, keys, and the USB pump with the full command set;
command items to A go through the current session (`b_send`).

Code: `target/usb_service.cpp` (`home_*`), `file_boot.cpp` and `key_ui.cpp` (`XYF_ONE_SESSION`),
`storage_server.cpp`, `peer_services.cpp` (RPC mode), `menu_link.cpp`. Home, reads, dry runs, reboots,
writes, set default without a reboot, and install from home have run on the device.

## Writes

Boot state uses power-safe A/B slots (`state-a.txt` / `state-b.txt`).
Sequence on A: read-only unmount → stock callbacks → read-write mount → write ≤ 4 KiB → read back →
read-write unmount → gates back → read-only remount. Writes are exact-path checked and happen only on an
explicit user/host action, never on a normal boot. The boot partition is written only by the stock
writer `88a06a7e` (commit-last A/B, power-loss safe).

### A admission rehearsal (#8, phase 1)

Product developer command `dev.a_rehearsal` transfers the current verified USB staging input from
B's `0x8b820000` into **A's separate RAM** at the same address. It never invokes a writer. The
cooperative B pump leaves one bounded file-client request out per pass; USB, menu and status keep
running while A is delayed. Shipping B install/uninstall keep their existing path.

A owns its existing file-boot staging exclusively during begin/chunk/finalize/proof/abort. Begin
revokes normal CHECK preparation before mutation; competing commands/requests revoke the lease
and are refused before scratch, mount or file work. A checks version, token replay, exact contiguous
offsets/size, chunk CRC, its own complete CRC/SHA and the existing install or stock-uninstall
admission policy. The extra padding sector remains within the original reservation. An admitted
install buffer never becomes a BOOT/default prepared image. Success collects A's digest and then
aborts the lease; failure, cancellation, timeout and session end also release logical ownership.
Transport failure keeps the existing lost-link behavior, with no reset, retry or fallback.
