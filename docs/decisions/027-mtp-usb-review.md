# 027: Review of the MTP USB driver's stock calls (plan 3.3)
Independent review of `MTP_USB_CALLS` (xyf_build.py) against the 1.2.1 bootloader listing
(the private `analysis/bootloader-121-listing`, `linear121.ann.asm`). Call closure computed by
a flow-following walk (branches followed to RTS, CALL targets recursed, indirect calls listed).
FORBIDDEN_TARGETS, the `889f*` storage range, `11a0*` SPI and the updater/companion entries of
`re/mtp_reach.py`: **none reached by any of the eight**, including the indirect targets below.
## Per address (all are function entries; 88a08f08/88a08f24 put 1-2 moves before LINK)

| Address | Verdict | Evidence |
|---|---|---|
| 88a09d5c register | confirmed | leaf; slot `[0x11803af0]` set if empty, 0 / -1 |
| 88a09d44 unregister | confirmed | leaf; clears the slot only if it equals arg |
| 88a092ee alloc | confirmed | finds ep by address in dev's config (0 if absent), 0x30-byte urb via 88a135e4, buffer via 88a0926c; frees urb if buffer fails |
| 88a08f24 submit IN | confirmed | -1 if urb or [urb+8] null; [urb+0x28]=0, queue 88a08e98, start 88a08d16 -> controller op (indirect, 88a0c0b0 TX start) |
| 88a08f08 recycle | confirmed | [urb+0x24]=0, [urb+0x28]=3, append 88a08e98; nothing else |
| 88a09418 free | confirmed | releases buffer (static pool slot or heap) then urb (88a13628) |
| 88a094aa kick | confirmed | completes an active+done urb (88a0946e -> driver urb_sent), then pops queue head 88a08ee0 |
| 88a0ba08 RX service | confirmed, with care | ep n via table 0x11803cc8; reads RxCount 0x200d0108+0x10n, FIFO copy 88a0b434, completes via 88a08f4e -> driver recv_urb; **n==0 takes the EP0 path** (writes 0x200d0102); never call with n=0 |

Closure: USB core 88a08d16..88a0c12c only; stock heap 88a135e4/88a13628 -> 88a2ec6c..88a2f260;
boot ROM 04022ea8 (from the alloc wrapper, after malloc) and 0402287c (FIFO copy); indirect: TX
start 88a0c0b0 and our callbacks urb_sent / recv_urb / recv_setup (88a09cf8, EP0 only).

Interrupts/locks: none of the eight mask by itself. Only short straight-line CLI/STI pairs in
88a0b294, 88a0b2b2 and 88a0c0b0 (bounded). No locks, no waits. **The heap (88a2ec6c..) has no
lock and no masking**: not reentrant against the USB ISR (which mallocs at SET_CONFIGURATION, 002).
Memory: urb 0x30 B + buffer, both stock heap (malloc wrapper: size+0x23, 32-byte aligned, raw
pointer at -4). Buffers <= 0x190 on endpoint 5 come from a 2-slot static pool (0x11b002b0, flags
0x1180291c); our endpoints 0x0a/0x89/0x83 never use it.
## Spans
All eight entries and their closure are inside `target/mtp_spans.hpp` except: (1) heap free body
88a2ed30..88a2ee14 (covered by server/peer spans, acceptable as in `check_mtp_spans`); (2) TX start
88a0c0b0..88a0c12e: its own set `target/mtp_usb_spans.hpp` (3.4), read only by the MTP USB mode, because
the product CRC-checks `mtp_spans.hpp` at run time. Census 2026-11-15: 128 bytes peeked on B (build 182)
equal the 1.2.1 image (CRC 0xcd2ec45b); B, where the driver runs, checks the span before registering.
## Approval scope
Register, unregister, allocate, send (submit IN), free, recycle fit the scope for USB function-driver calls.
Kick (88a094aa) and service-RX (88a0ba08) did not (interrupt-path routines run from the main loop, record
007); both are allowed for MTP only, first in a test build with one device test. Changing this list needs
the developer's explicit yes. 007's own-FIFO read
names no FIFO data register, so it is not implemented.
## Misuse in target/mtp_usb.cpp (fixed in 3.2b)
1. Unmasked heap calls: send() failure and all of free_ours() masked (at most kRing = 8 free_urb calls);
   the stack model fires the interrupt inside any unmasked heap call (`unmasked-free` case).
2. Deconfigure/stop (0xa/0xf, dispatcher 88a08c7c) and SET_CONFIGURATION/SET_INTERFACE flush every endpoint
   (88a08ab4 -> 88a095a8): OUT queued+active freed without callback, IN and EP0 completed through urb_sent
   status 2. Bus reset (4) does not flush. Queued urbs are never ours; the driver frees only ring-held ones.
3. `on_urb_sent` returning 1 = "stack frees", consistent with 88a095a8.
4. Kicks: compiled only with XYF_MTP_USB_KICK; without them the model stalls after 49664 bytes.
5. The 88a0ba08 binding refuses endpoint 0 and anything but bulk OUT before the call.
## Test build (3.4)
Mode `xyf-mtp-usb` (builder: the product's feature sets via `product_kind`, plus `XYF_MTP_USB` and the
kicks); the builder admits MTP_USB_CALLS and the kicks only there, with both span sets checked and the
pid.codes test pair 1209:0001 (private tests; the shipped pair is undecided: stock MTP and CDC both
declare 2367:0021). Interface/config record layouts match the stock MTP driver 88a12534 [L]. Switch [L]:
stop(1) unregisters stock CDC (88a13772 -> 88a136f6 -> 88a132d0 -> 88a09d44(88a1349c)) after start(0x400),
and start(0x400) fails if the slot is taken, so B leaves by stop(1), unregister, then start(0x400).
Probe: `diag.mtp_usb` 0x62 (dev, not published), `scripts/mtp_usb_probe.py`.
