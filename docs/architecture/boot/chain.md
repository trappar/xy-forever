# Boot chain and flows

Part of [boot architecture](../boot.md).

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
  (manifest size/CRC, LDR policy) and the fingerprint (below) → serves it → both boot it.
  No USB, never waits for a computer. A default that can't boot opens the menu with the reason.
- **PLAY at power-on:** the boot menu with USB on.
- **USB service:** entered after XY Forever's own reboots (reboot marker at SDRAM
  `0x8bfff000`); host commands in [operating/host-tool.md](../../operating/host-tool.md).

## Boot model v3 (proven)

| Situation | Behaviour |
|---|---|
| Plain power-on | straight to the default OS, as fast as possible; no USB; in doubt: converge first (below) |
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
