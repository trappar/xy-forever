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
- Names from the table: 6..9 `KTrack1..4`, 10..13 `KEncClick1..4`, 51 `KPlay`, 52 `KStop`,
  53 `KLeft`, 54 `KRight`, 55 `KShift`.

### What the menu sees (#288)

The menu logs its last 32 raw input events: `uv run xyf-host inputs` (`--watch`: each new one as
it comes). Each key event as the stock callback gives it (id, press or release; the host's `key`
command marked "(host)"), and each record of an accepted knob frame (id, signed count), on the
menu's millisecond clock (`target/knob_input.hpp` `InputLog`, diag.status block INP1).

Checked on the device (2026-11-23, build `242505c6`):

| Question | Answer |
|---|---|
| H1: the four buttons under the screen (m1..m4) | keys 6, 7, 8, 9 |
| H2: m4 sends a release; SHIFT (55) held with an m key | a 2.9 s hold of m4 arrived as press, then release; SHIFT + m1 arrived as both keys |
| H3: knob clicks | their own keys, apart from turns and from each other: knob 1 = 10, knob 2 = 11, knob 3 = 12. Today's menu reads every click as PLAY |
| H4: the fourth knob's turns | records with id 4, one count each, two per detent (below) |

## Knobs (B, through the PSoC)

Pushes are keys 10..13 (`KEncClick1..4`). Turns are frame type `0x45`: three-byte
`[id][signed int16 big-endian delta]` records, ids 1..4. `knob_input` reads only consumed
stock queue slots after polling; it sends no PSoC commands. Queue `0x88a522fc`: 128 slots of
0xfd bytes, read/write indices +0x7e80/+0x7e84. Accept route 0x41, len <=39 divisible by 3;
reset the cursor on invalid indices/restart. Unknown ids do not control the menu.

One detent is two counts: a slow turn of knob 4 logged ±1 records in pairs 30–75 ms apart, pairs
0.4–0.6 s apart (#288). The stock bootloader has no knob consumer (its poll drops type 0x45 frames), so
this is the only count. The menu moves one row per 2 counts of all knobs together
(`knob_input.hpp` `counts_per_row`, 2 since build 178), so a detent is one row.

## Display (B)

- Frames go to `0x8c000000` and are posted to the mailbox at `0x11905f30`.
- The vsync PINT callback `889efc3a` never runs at cold time, and `smc_dma_active`
  (`0x11905cc0`) is stale (= 1).
- **Present synchronously, like stock `88a1de7c`:**
  1. bounded DMA-idle check (`0x200a3030 & 0x700`)
  2. `889eed4c`
  3. clear the stale busy flag
  4. `889eea28`
  5. `889efa6a(fb, 1)`: with 1 it returns only once the display DMA is idle again (its loop at
     `889efc12`), so the whole frame is sent before present returns.
- Zero the `0x3c000`-byte framebuffer before the first frame, or text appears over
  the logo. The stock text console is `889eeab2`.
- **Frame time (#288):** `uv run xyf-host frames` (`key_ui.hpp` `Frames`, diag.status block FRM1):
  the repaint (one full 480x216 RGB565 frame and its cache flush), the stock transfer, and the whole
  present. Measured on build `efb07ddc`: repaint 2.2–2.5 ms, transfer 15.1 ms (it waits for its own
  DMA), whole present 17.3–17.6 ms, with the menu loop blocked throughout. So a frame costs about 17 ms:
  at most 57 frames/s with nothing else running. A moving progress bar or a spinner fits at a few frames
  per second; each frame still holds up USB and key handling for 17 ms.

### Where codes (#114)

Each blocking wait on B sets a where code (`target/where.hpp`; product builds only). A wait still running after
500 ms while the menu has the screen paints `W<code>` in the top-right corner once, from its own loop through the
menu's present (never from an interrupt), and the menu redraws without it when the wait ends. Normal waits paint
nothing. The crash screen names the wait B was in, else the last one it left.

| Code | Wait |
|---|---|
| W1 | file client: A's answer to a file request (`await_answer`) |
| W2 | link settle: the outstanding echo before B's next request |
| W3 | A's answer to a command item (`b_ask`) |
| W4 | A's check status blob (`b_check`) |
| W5 | the catalog blob |
| W6 | the stage-2 blob (the stage-2 test modes, built without where codes: reserved) |
| W7 | the link handshake (before the menu) |
| W8 | a link user waits for the catalog request in flight (`fetch_wait`) |
| W9 | the open request before B resets (`link_close`) |
| W10 | the boot image transfer (after the menu) |
| W11 | a USB reply into the CDC |
| W12 | an install log line over USB (never paints: 200 ms bound) |

A frozen screen, a USB link that stops answering, and the PSoC frames that can stop B (#314):
[B stops](b-stops.md).

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
  invalidates the checkpoint, so the next boot rescans (a full scan has taken 16.85 s).

## User storage and recovery hazards

The OS owns `/fat32` on the same eMMC user area as yaffs; its MTP exposes projects,
samples and presets there. XY Forever and dev-menu MTP expose `/yaffs2`, and cannot
read those files through the bootloader's FatFs (which serves a RAM disk only).
XY Forever access to projects/samples is outside the product scope.

The stock OS formats `/fat32` automatically if its mount fails. FAT-sector damage or
factory reset can therefore destroy projects without a confirmation at the next boot;
this is not a repair path for user data. The stock dev-menu factory reset erases the
FAT boot sector as well as `/yaffs2/user`, logs and content packs. No raw FAT write,
erase or partition-select path is authorized by these observations.
