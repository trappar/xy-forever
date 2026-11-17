# Hardware and stock-code facts: keys, display, storage

Part of [Hardware and stock-code facts](../HARDWARE.md).

## Keys (B, through the PSoC)

- Keys arrive only through the PSoC on B. The stock key-status reply (request 0x41)
  stays at `0x8807ce7e` as `[2 hdr][0x42][len 0x0a][10-byte bitmap]`.
- Bit i is key enum i (table `0x88a25584`): **KPlay = 51**, **COM/KUsb = 24**. "-"
  and "+" are KLeft/KRight.
- Live key frames (type 0x42) reach the callback at `ctx+0x813c`, which `88a245dc`
  sets as (key, pressed).
- **Links:** the boot teardown `88a35540` closes the PSoC and USB-serial links. The
  poll `88a24922(0x88a522c8)` reopens them and greets.

## Display (B)

- Frames go to `0x8c000000` and are posted to the mailbox at `0x11905f30`.
- The vsync PINT callback `889efc3a` never runs at cold time, and `smc_dma_active`
  (`0x11905cc0`) is stale (= 1).
- **Present synchronously, like stock `88a1de7c`:**
  1. bounded DMA-idle check (`0x200a3030 & 0x700`)
  2. `889eed4c`
  3. clear the stale busy flag
  4. `889eea28`
  5. `889efa6a(fb, 1)`
- Zero the `0x3c000`-byte framebuffer before the first frame, or text appears over
  the logo. The stock text console is `889eeab2`.

## Storage (A, yaffs)

- No YAFFS device is registered at cold entry on a normal boot (the sentinel at
  `0x11907f8c` is empty). Construct one: `889f9700` → `889f963e("/yaffs2", 0, 0x11ff)`
  / `889fb24e`. Read-only mount: `889fae2e(dev, "/yaffs2", 1, 0)`.
- **Controller:**
  - It is already open (`0x11907ab4` = 1). Init `88a3434e` is a no-op; shutdown
    `88a344a8` really closes it, so leave it open.
  - Driver slot +0x8c (`0x88a34348`) is a `JUMP.S` to itself, i.e. a hang.
  - The tag-callback install `88a030a0` fills only empty slots, so the deny thunks
    at +0x9c/+0xa8 survive.
- **Avoid stock stdio and opendir.** They go through the provider map `0x8803f6b8`,
  which only `provider_mount` fills, and that mounts read-write. Use the direct
  yaffs calls instead. Provider vtable: +0x10 (`889fbb0c`) is yaffs_write; +0x14
  (`889fb928` → `889fb760`) is read.
- The malloc data word `0x88a2f264` differs on the device, so it is excluded from
  the CRC gate.
- **Paths:** the stock open prepends `/yaffs2/` only to relative paths. Absolute
  paths must start with `/yaffs2/` or `/fat32/`, or the open fails ("OTP FILE OPEN
  FAILED").
- **Mount time:** the read-only mount takes 55–63 ms with a checkpoint and 4.5–5.1 s
  on a full scan. A full OS read took 487 ms (about 4.9 MB/s). A dev-menu MTP copy
  invalidates the checkpoint, so the next boot rescans (16.85 s once, after 087).
