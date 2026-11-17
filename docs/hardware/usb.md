# Hardware and stock-code facts: boot partition and USB

Part of [Hardware and stock-code facts](../HARDWARE.md).

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

## Stock USB function-driver API (unverified)

From an offline study that was never re-checked or device-tested;
decision 002 redoes it. Stock MTP (`88a0ecda..88a12478`) is one block run from the USB
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
