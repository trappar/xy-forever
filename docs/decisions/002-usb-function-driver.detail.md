# 002: Stock USB function-driver API and upload pacing (detail)

Detail of [002-usb-function-driver.md](002-usb-function-driver.md).

## Answer

5. **Entry points** (all [L]; mask the USB interrupts around every main-loop call: the stack's own queue code and its SET_CONFIGURATION malloc run unmasked in
   the interrupt [L/I]). Static reach of rows 1-8 together (plus the indirect TX start `88a0c0b0`): USB core/controller `88a08a6c..88a0c0b0` (for `88a0ba08`
   this includes the EP0 standard-request path, not taken for n != 0) and the stock heap (`88a135e4`, `88a13628` -> `88a2ec6c..88a2f110`, L2 `0803e324/e374`,
   abort `0803dfc4`, ROM `0402xxxx`); **no storage client (`889f*`), mmc, SPI flash, updater, peer link or FORBIDDEN_TARGETS** (`re/reach.py`, controller op
   table `0x11803d00` added by hand).

| Addr | What | Args -> return | Called from | Reaches |
|---|---|---|---|---|
| `88a09d5c` | register driver | drv -> 0 / -1 | main, before start | nothing (leaf) |
| `88a09d44` | unregister driver | drv | main, after stop | nothing (leaf) |
| `88a092ee` | allocate urb + buffer (endpoint must be configured) | dev, ep addr, len -> urb / 0 | main | stock heap |
| `88a08f24` | submit IN urb (queue + start TX, op `88a0c0b0`); **never for OUT** (start is the TX path) | urb -> 0 / -1 | main or interrupt | MUSB TX FIFO/DMA `88a0b79e` |
| `88a08f08` | hand an OUT urb back (length 0, status 3, append to queue) | urb | main or interrupt | queue only |
| `88a09418` | free urb + buffer (only urbs we own: not queued/active) | urb | main | stock heap |
| `88a094aa` | kick 1: idle endpoint takes its queue head | ep object | main, masked | queue; `urb_sent` only if active+done |
| `88a0ba08` | kick 2: service a parked RX packet (only if RxPktRdy) | ep number | main, masked | MUSB RX FIFO `88a0b434`, `recv_urb` |
| `88a35044`/`88a34fec` | start(0) / stop(1) (already in USB_CALLS) | flags | main | as today |

Notes [L]: the stack frees urbs that are queued or active on OUT at reconfigure, deconfigure (incl. suspend) and stop without a callback (`88a095a8`): forget
them; held ones stay ours. Avoid endpoint numbers 5 (static 0x190-byte buffers in `88a0926c`) and 6 (async DMA in `88a0b434`); the ISR serves 1-11. Use stock
MTP's `0x89`/`0x0a`/`0x83` (above 8: PIO, no DMA cache work).

## Earlier claims (hardware/usb.md "unverified")

Right: slot, register/unregister, ops order, endpoints `0x89`/`0x0a`/`0x83`, send = alloc/fill/submit/free as `cdc_write`, recycle `88a08f08`, start =
register + `88a35044(0)`, the open risk. Wrong or incomplete: the driver words +8/+0xc are a devinfo record and a config-record array (not descriptors),
+0x10/+0x14/+0x1c are filled by the stack (struct must be writable), +0x18 is the configuration count; "one packet per urb" holds only for the stack's own OUT
urb; the callback return values, the ZLP flag and the "recycle does not re-arm" detail were missing. New: every stock driver (MTP, CDC, MassStorage) declares
64-byte bulk endpoints although start sets POWER.HSEnab (`88a0bffa`); high speed requires 512.
