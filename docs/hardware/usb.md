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
  resets at most ~0.1-1.4 s before B or any time after.
- **Forbidden targets** (never reachable, enforced by the build):
  - SPI-NOR erase/write: `11a062c6`, `11a06376`, `11a06470`
  - SPI metadata: `11a05312`
  - updater: `88a2863c`, `88a2775e`, `88a2830e`
  - companion programming: `88a2742c`, `88a2756e`, `88a26f90`, `88a26a80`, `88a27278`
  - stock `.tfw` pipeline: `88a25d28`, `88a25c6c`, `88a2901a`, `88a28cfc`, `88a27d00`,
    `88a196cc`, `88a18cae`, `88a1871c`, RAM disk `88a14820`, `11a05340`, `88a21456`, `88a34ed0`
  - boot ROM: every entry but memcpy `040201c4` and the OTP read/init below

## Stock USB function-driver API

The implementation is `target/mtp_usb.hpp`, `mtp_usb.cpp`, `mtp_usb_stock.cpp`; its host stack model
is `mtp_usb_test.cpp`. These contracts come from the 1.2.1 listing and are enforced by those tests;
USB timing and host enumeration still require device qualification.

- One writable driver slot `[0x11803af0]`: register `88a09d5c`, unregister `88a09d44`.
  Register before start `88a35044(0x200)`: bit 9 builds CDC functional descriptors without
  registering a stock class. One composite exposes CDC interfaces 0..1 and MTP interface 2;
  the stock CDC/MTP combination collides on endpoint numbers. See the
  [transport identities](../protocol/transport.md) for current VID:PID and fallback.
- `ConfigDesc` is **0x18 bytes**, including `n_iads` at +0x10 (at most 4) and the IAD array at +0x14.
  The stack writes driver fields +0x10/+0x14/+0x1c. Layouts and endpoint numbers live beside the
  structures in `mtp_usb.hpp`; do not infer them from USB wire descriptor sizes.
- Start once per boot. Stop `88a34fec(0)` keeps GP timer 7 open; nonzero closes it. Controller init
  waits five timer ticks with no bound, so closing/reopening that timer inside a boot can hang B.
- Most callbacks run in the USB ISR; stop can also complete IN callbacks in the main loop.
  `recv_urb` returning 0 retains an OUT urb; nonzero requeues it. `urb_sent` returning nonzero frees
  the IN urb. Callbacks queue work; filesystem and MTP execution stay in the main loop.
- SET_CONFIGURATION, SET_INTERFACE, deconfigure/suspend and stop flush endpoints: queued/active OUT
  urbs are freed without callbacks; IN completes through `urb_sent` with status 2. Bus reset alone
  does not flush. Free only ring-held urbs; an urb from an old configuration must be freed, never
  recycled. Suspend/resume needs OUT rearming even without another SET_CONFIGURATION.
- Stock heap allocation/free is not reentrant with the USB ISR: mask interrupts around every main-loop
  heap call and ring ownership change. Submit IN `88a08f24`, recycle OUT `88a08f08`, allocate `88a092ee`,
  free `88a09418`; the builder scopes these calls and checks their CRC spans.
- OUT buffers must be multiples of 512: a packet exceeding the remaining space is truncated.
  Recycling only appends to the queue; a parked packet needs kick `88a094aa` and RX service `88a0ba08`.
  These interrupt-path calls need separate explicit approval; never service endpoint 0, and service
  only a parked bulk-OUT packet. Without the parked check, the call invents a ZLP completion.
- Reuse the successful boot-time USB identity check when MTP starts: the live serial service changes
  words inside `usb_spans.hpp`, so repeating it rejects valid code. Keep both MTP-specific span
  checks before registration (`mtp_usb_gate.hpp`); a mismatch refuses the start.
