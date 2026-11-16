# Hardware and stock-code facts

Reference for the OP-XY and its stock 1.2.1 bootloader, as used by XY Forever's
cold code. Addresses are processor addresses in the 1.2.1 bootloader image unless
noted. The full listing lives in the private folder (`LISTING_121` in
`xy_forever/paths.py`); evidence is cited by decision record. Verify claims against the
raw listing: Ghidra's decompiler C is unreliable for parallel instructions and indirect jumps.

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

Same on A and B (separate SDRAM). Evidence for the free gap and the hand-off: [ARCHITECTURE.md](ARCHITECTURE.md#memory-map-product-build-171).

| Range | Owner, use | Evidence |
|---|---|---|
| `0x88000000..0x88a63bf8` | stock bootloader image, data, BSS | static segments (`check_boot_reservations`) |
| `0x88a65bf8..0x8aa65bf4` | stock allocator arena (yaffs device, caches) | allocator table `0x88a2a4d0` (build-checked) |
| `0x8a000000..0x8a800000` (+64 KiB margin) | stock stream buffer: the boot loader reads `os.ldr` here (8 MiB cap, `88a34a12`); our boot copy (staging -> here, <= `staging_bytes` 0x7e0000 B) for the RAM loader `88a3477a` | listing; `cold.cpp` protected |
| `0x8ab00000..0x8ab80000` | XY Forever code + data (512 KiB window; 172 uses ~166 KB) | linker `ASSERT`, `verify_elf` |
| `0x8ab80000..0x8ab84000` | XY Forever stack (16 KiB; entry sets SP `0x8ab84000`) | entry audit (P0.H/P0.L) |
| `0x8ab90000..0x8ac00000` | stage-2 window (dormant loader, B only) | `check_stage2_image` |
| `0x8ac00000..0x8b000000` | unused | |
| `0x8b000000..0x8b800000` | B's mod job: 64 KiB state, then the work region (5.4d2; 6.3 MiB peak); 3.5b MTP session state (33 KiB, sim only) | `cold_layout.hpp`, `check_memory_map` |
| `0x8b800000..0x8c000000` | our scratch, catalog, USB frames, staging (B 7 MiB image buffer), reboot marker `0x8bfff000` | |
| `0x8c000000..0x8c03c000` | B's framebuffer | |
| `0x8d3be380..0x8d4d0380` | census, reporter, carry slot (inside the OS image's heap) | |

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

## Boot partition and USB (B)

- **Boot partition:** written only by the stock `mmc_tools` writer `88a06a7e`
  (commit-last A/B, power-loss safe). XY Forever's INSTALL allows A exactly the
  Track-5 ops, with read-back verification and a confirm token.
- **Reach audit:** a reach audit of 560 functions found no SPI-flash, metadata,
  updater or companion call.
- **USB:** CDC serial on B. smxUSBD start `88a35044` / stop `88a34fec`. The stale
  stock CDC callback `11a03e04` must be detached. USB stops before every reset (harmless,
  never shown needed). B's reset itself always completes; its next boot hangs unless A
  resets at most ~0.1-1.4 s before B or any time after (record 022; 104-108, 148-162).
- **Forbidden targets** (never reachable, enforced by the build):
  - SPI-NOR erase/write: `11a062c6`, `11a06376`, `11a06470`
  - SPI metadata: `11a05312`
  - updater: `88a2863c`, `88a2775e`, `88a2830e`
  - companion programming: `88a2742c`, `88a2756e`, `88a26f90`, `88a26a80`, `88a27278`
  - stock `.tfw` pipeline (003): `88a25d28`, `88a25c6c`, `88a2901a`, `88a28cfc`, `88a27d00`,
    `88a196cc`, `88a18cae`, `88a1871c`, RAM disk `88a14820`, `11a05340`, `88a21456`, `88a34ed0`
  - boot ROM: every entry but memcpy `040201c4` and the OTP read/init below

## Stock storage server requests (role 0, as MTP uses them)

Classes by vtable; A's executor runs the op and replies. **Some classes always send a
data body, and B waits for it without a timeout**, so a refusal must have the same shape.

| Class (vtable) | Ops | Reply body |
|---|---|---|
| partition `0x11907694` (`889f76ca`) | 8 mount, 9 unmount, `0x2c` lseek, `0x2d` close, `0x30` closedir, `0x34` fsync, `0x36` truncate (by descriptor); 4 / `0x14` / `0x18` = raw partition layer | none |
| path `0x119076b4` (`889f6fea`) | `0x21` access, `0x25` unlink, `0x26` mkdir, `0x27` rmdir, `0x28` yaffs sync, `0x29` open (flags at +8), `0x2e` opendir, `0x35` set times | none |
| query `0x11907388` (`889f72e4`) | `0x22` stat, `0x23` statfs, `0x31` free space (64-bit) | always: `0x34`, `0x0c`, 8 bytes |
| sector `0x11907348` (`889f6b7c`) | `0x2f` readdir (result: A's dirent address, 0 at the end, `889f6c02`; never 1); `0x11` / `0x12` boot-partition sector reads | always: `0x10c` (also at end of folder); `0x200`·n |
| read `0x11907368` (`889f6cd6`) | `0x2a` read | streamed |
| write `0x11907710` (`889f6dca`) | `0x2b` write; `0x15` / `0x16` boot-partition sector writes | none |

There is no rename and no format request. A's slave interrupt answers the link handshake
(ops 1 and 10) by itself in every mode, so a B that restarts alone talks to the old A.

- **Restart order (stock boot, read from the listing; record 022):**
  - B boots A, not the reverse: early in B's bootloader (`88a1d34c`, before the display reset,
    USB and the link) B sends A `0x03`. A fresh A (boot ROM, `0xff`) gets the bootloader pushed
    and both go on; a running A (its bootloader or XY Forever) makes B wait for PA6 with no
    bound. So a lone B reset freezes (old frame on screen) until A resets; REBOOT works
    because A resets ~0.1 s first. XY Forever never resets B unless A follows or went first.
  - Both processors run the same main (`88a35668`) and boot routine (`88a34a00`). A
    (role 0) first serves in `88a35b90`: it polls until B's release (op 7) arrives, with
    no timeout, then reads `os.ldr` itself and boots. So A may restart long before B.
  - B's fetch `88a07240`: select partition 1 (op `0x18`); a select that returns an error
    makes the function return that error as the length, and B then "boots" whatever is
    at `0x8a000000`. A sector read (op `0x11`) that fails is retried forever.
  - B's request call (`889f869e` -> `889f7b12`) waits for the done flag with no timeout.
    XY Forever never calls it (forbidden since 158): B's file client builds the same requests
    with the stock builders and polls the done flag itself, with a bound (`file_client.cpp`).
    A request that the running A took and never answered is answered by A's bootloader
    after A restarts (Test 108 recovered that way). So in any mode A must never ANSWER a
    restarted B's boot requests: leave them and restart A. Since build 159 (plan 0.9b, record
    016) it holds in every mode: B's link open (op 10 `0x12345678`) after its first command,
    or a boot request (select, sector read) with nothing frozen outside install mode, is held
    unanswered, A closes storage and restarts; a B that loses A at the menu stays there (164).
  - The stock select `88a08710` uses only the argument's low 3 bits and passes them with
    `0xb3` to `88a0826c` [I: eMMC EXT_CSD 179, partition access]: a command request (`'CM'`
    tag) that reaches A's bootloader selects partition `value & 7`. B's command values keep
    those bits 0 (the user area).
  - The master queues requests (priority list at driver `+0xac`, active at `+0xb0`), but
    a request and its data body are not one unit: a second request submitted between
    them breaks the exchange (146). Only submit while no stock call can be in progress
    (B's main loop is safe; an interrupt above the USB interrupt is not).
  - A stock flag `B[0x88a1cdc1]` (set by the inter-CPU command handler `0x88a5a454`)
    makes a bootloader reset itself; unused by XY Forever.
- **WDOG0 as an interrupt (SEC source 3):** the expiry flag WDRO (`WDOG_CTL` bit 15, write
  1 to clear) clears only while the watchdog is disabled. Write the disable value first,
  then the disable value with bit 15, then reload and enable. Clearing it in the disable
  write leaves the request up: the handler re-enters forever as soon as it returns.
- **The display keeps its last frame** when B resets or hangs: a frozen screen does not
  tell which.
- **Dev-menu marker:** the file `/yaffs2/bootloader.dev` (4 bytes). `88a1d7e6` checks that
  it exists; `88a1d7a2` (command `0xac`) creates or removes it and sets the dev flag
  `B[0x88a1f3be]`. See SAFETY.
- **Upgrade resume at boot** (`88a3512a` -> `88a28ff4` -> updater `88a2863c`) runs only when
  the SPI metadata word read by `11a05244` equals `0xab0d`. Files in `/yaffs2/upgrade/`
  alone do nothing. The bootloader's update mode takes a `.tfw` from a FatFs RAM disk over
  USB mass storage, never from yaffs.

## Stock USB function-driver API (unverified)

From an offline study that was never re-checked or device-tested; plan
item 3.1 redoes it. Stock MTP (`88a0ecda..88a12478`) is one block run from the USB
interrupt; its only hook is the storage callback table (`0x88073380` -> `0x11906c48`).
Below it, a generic function-driver API that CDC also uses: one driver slot
`[0x11803af0]`, register `88a09d5c(drv)`, unregister `88a09d44(drv)`; driver =
`{name, ops, device_desc, config_desc, 0, 0, 1, 0}` (MTP `0x88a12534`, CDC
`0x88a1349c`); ops `{event, urb_sent, recv_urb, recv_setup}`. MTP endpoints (table
`0x88a1262c`): bulk IN `0x89`, bulk OUT `0x0a`, interrupt IN `0x83`. Send:
`urb = 88a092ee(dev, ep, len)`, fill, `88a08f24(urb)`, free `88a09418(urb)` (as stock
`cdc_write` `88a12eac`). Receive: `recv_urb` in the interrupt, one packet per urb,
recycle with `88a08f08(urb)`. Start: register, then `88a35044(0)`. Open risk: a held
bulk-OUT urb leaves the packet in the FIFO and nothing re-arms reception (check
`88a0bb44`, `88a0ba08`, `88a0b434`).

## Firmware packages (`.tfw`)

- **Format:** AES-256-CBC, with key and stream in their original byte order.
  - Offsets: header 0x80, key index 0x84, IV 0xf0, ZIP length 0x100, encrypted
    name at 0x380 (256 B, `firmware_secure.zip`), encrypted ZIP at 0x480.
  - CBC continues across the name and the ZIP; padding is PKCS#7.
- **Key indexes:**
  - 0: OTP ROM selectors 3/4 via ROM `0x04000048` / `0x11a05c9c`; verified.
  - 1: selectors 5/6; untested.
  - 0xff: a fixed constant at `0x88a1b97c`.
  - The decrypt routine is `0x88a18cae`. Keys: the private `keys/` folder (never
    print or commit them).
- **Signatures:** the signing (private) key is unknown, so stock rejects any custom
  `.tfw`. The old bootloader also rejects unsigned and legacy containers; key index
  `fe` is no bypass. The vendor public key (signature key index 0) was recovered
  from the three official signatures (plan 4.3b; one common key, verified by our
  verifier and OpenSSL): `keys/opxy-tfw-public-key.json` in the private folder. Not yet read from
  a device (OTP selector 9; `tfw.check`, plan 4.3e: `scripts/tfw_probe.py`).
- **OTP (read from the 1.2.1 listing).** Boot-ROM jump table, 4-byte entries:
  `0x04000008` ROM loader (via `88a3477a`); `0x04000048` read (R0 selector, R1 buffer; R0 the
  answer); `0x0400004c` (only `88a2123c`: selector 0x13, a 16-byte buffer filled before the call
  and read after; a challenge-response, not a key read [I]); `0x04000050` never called;
  `0x04000054` init (R0 = 0). Stock wrappers: init `11a05a32` (once per boot, guard byte
  `0x11b01650`), read `11a05a1e`; public key `11a05b22` (sel 9, answer != 0, 0x38 B; sel 10 for
  index 1), AES key `11a05c9c` (sel 3 to scratch, sel 4 to scratch+0x10, each answer == 1, 0x20 B;
  5/6 for index 1). Scratch: SDRAM `0x8803d8c4` (0x200 B, cleared before, not after).
  Callers: the boot fetch `88a07240` on both processors when the boot image is encrypted
  (`88a06a0c`: AES key to `0x88072de0`), the Track-5 writer `88a06a7e` on B every install, update
  mode on B (`88a18cae`, `88a17f2a`: sel 9). So sel 3/4 already run in XY Forever's context on B;
  sel 9 never has. XY Forever's reader: `target/otp_keys.cpp` (B, `tfw.check` only).
- **Companion images:** CYACD has 367 data rows. BLE updates go through a TE
  internal bridge, not the plain Nordic DFU transport.
- **SPI metadata** (record at SPI `0xc000`, member +0xc at `0x8803d890`): `factory:%u`
  is a 16-bit field. The dev-menu gate is software state, not OTP, and no
  retail-to-dev method exists.
- **Stock signature check** (`0x88a17f2a`, read from the listing; confirmed on the
  three packages): signature key index `+0x89` = 0 selects the vendor key, 56 B
  from OTP selector 9 via `11a05b22`, Qx || Qy big-endian (1 = per-device key,
  selector 10, and 0xff = dev constant `0x88a1b99c` need dev mode). SHA-256 (L1
  `11a02102/25aa/2656`) of the 0x300-byte header with `+0x10..+0x50` zeroed; ECDSA
  P-224 (`11a03148`, PKA; curve constants `0x11b00ea4..0x11b00f30`) on its leftmost
  28 bytes; r `+0x14`, s `+0x34`, 28 B big-endian (`11a02b86`). Driver `88a1871c`:
  format, signature, SKU (`88a18288`), payload length `+0x98` and SHA-256 `+0x9c` of
  everything after the header (`88a1844a`). Native: `native/p224`, `tfw_signature`
  (host-tested, not linked); the L1 wrapper's data-load CRC checks cannot be used.
- **Companion update paths:** the stock updater resumes through the SPI marker, which
  XY Forever may not write. The stock backplane handler polls a generic message
  dispatcher with a conditional path to the SPI metadata writer, so skipping the
  stock orchestrator does not make it SPI-safe. Static findings only: records 003
  and 015.
- **Package contents (official ZIPs, compared offline):** 1.2.1 → 1.2.5 changes the OS,
  backplane (367 rows) and BLE application (244,816 B); all 839 content files
  (125,790,852 B) are identical. 1.1.33 has 829 content files: 825 identical, 3
  changed, 1 added, 11 only in 1.2.1; its firmware label is 1.1.33+1, its OS 1.1.33+0.
  Skip a content write only after comparing the device's actual files.
- **Host tools:** `../tools/tfw-decrypt/`; `xyf_package.py ZIP [--baseline ZIP]
  [--stage DIR]` reviews and stages a decrypted vendor ZIP (host only, unauthenticated,
  bootloader members never staged).

## Reverse-engineering tools

- **Listing tools:** `re/bootloader.py ADDR [-d N] [-a]` summarises functions;
  `re/reach.py ROOTS` lists reachable functions; `re/spans.py REACH...` generates CRC
  spans; `re/mtp_reach.py` runs the MTP reach and forbidden check. They read the
  private listing and image, plus census reports from
  `REPORTS` (`xy_forever/paths.py`).
- **Ghidra:** `../tools/ghidra_12.1.4_PUBLIC` with the Blackfin module
  `../tools/Ghidra-BlackFinPlus`, user dir `../tools/ghidra-user`, JDK
  `../tools/jdk-21.0.13+9`.
  - Projects: one for OS 1.2.1 and one for the 1.2.1 bootloader, kept under
    `ANALYSIS` (`xy_forever/paths.py`).
  - Never run two writers on one project. REA has no Blackfin provider.
