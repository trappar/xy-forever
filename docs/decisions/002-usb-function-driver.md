# 002: Stock USB function-driver API and upload pacing

Research record, 2026-11-14, from the 1.2.1 bootloader listing only (no device). **[L]** = read from the listing, **[I]** = inferred (MUSB behaviour or reasoning), to be proven by 3.2-3.4.

## Question

How does a private function driver plug into the stock USB stack (smxUSBD), can the main loop hold back a multi-megabyte bulk-OUT upload, and which stock entry points would the builder's allowlist need?

## Answer

1. **API.** One driver slot `[0x11803af0]`. Register `88a09d5c(drv)` stores `drv` if the slot is empty (returns 0, else -1); unregister `88a09d44(drv)` clears it only if it holds `drv` [L]. `88a35044(0)` (flags 0: no stock driver, `88a13676`) builds descriptors from the slot driver (`88a096c4` -> `88a0acc0`); the stop's teardown (`88a0ae14`) frees them through the slot, so the order is register, start(0), ..., stop, unregister [L].
   - Driver (0x20 B, **writable**: the stack fills +0x10/+0x14/+0x1c at start): `{name, ops, devinfo, configs, -, -, n_configs, -}` [L]. devinfo (0x14): class, subclass, protocol, pad, VID, PID, manufacturer, product, serial strings. Config (0x10): name, bmAttributes, bMaxPower, .., n_ifaces, ifaces. Interface (0x10): name, class, subclass, protocol, pad, n_alts, alts. Alt (0x18): name, alt number, n_extra, extra descriptors (emitted between the interface and its endpoints), n_eps, eps. Endpoint (8): addr, attr, W wMaxPacketSize (copied verbatim, `88a0a710`), interval, dir byte [L].
   - ops `{event(dev,code), urb_sent(urb), recv_urb(urb), recv_setup(ep0urb)}` [L]. `urb_sent`/`recv_urb` return 0 = driver keeps the urb, nonzero = stack frees (sent, `88a0946e`) or re-queues (received, `88a08f4e`) it [L]. `dev` comes with every event (R0) and is `[urb+0xc]` [L].
   - Event codes [L]: 1, 2 at start and 4, 0xd, 0xe, 0xf at stop (main loop); 3, 4 bus reset, 5 address, 6 SET_CONFIGURATION, 7 SET_INTERFACE, 8/9 endpoint feature, 0xa SET_CONFIGURATION(0) **and suspend or disconnect**, 0xc resume (USB interrupt).
   - urb (0x30, zeroed stock heap): +8 ep object, +0xc dev, +0x18 buffer, +0x1c buffer size, +0x20 length wanted, +0x24 length done, +0x28 status, +0x2c "end with ZLP" [L]. Endpoint object (0x18, `[0x11803cc8+4n]`): +0 address, +4 urb queue, +0xc active urb, +0x12 max packet [L].
   - Context: `recv_urb`, `urb_sent`, `recv_setup` and most events run in the USB interrupt (`88a0bdd8` -> `88a0bb44`) [L]; `urb_sent` also from the flush at stop (main loop) [L].
2. **Pacing.** At SET_CONFIGURATION the stack arms exactly **one** OUT urb per OUT endpoint, one max packet long (`88a09612`) [L]. A driver may queue more (alloc with `88a092ee`, queue with `88a08f08`); a urb completes when full or on a short packet (`88a0ba08`) [L]. With no urb armed, the receive handler returns without touching the FIFO (`88a0ba08`: `[ep+0xc]==0`), the ISR clears its pending bit anyway (`88a0bb44`, `W[0x11803cfa]`) and the RX FIFO is single-buffered (`88a0aefc` called with 0) [L]: so the packet stays parked, the controller NAKs the host, nothing is dropped or allocated [I: MUSB NAKs while RxPktRdy is set]. **But nothing resumes it**: `88a08f08` only appends to the queue (no hardware access, endpoint stays idle) and no new RX interrupt comes [L; I for the interrupt]. A packet longer than the space left in a urb is cut and the rest discarded (`88a0ba08` clamps, `88a0b434` clears RxPktRdy) [L]: urb sizes must be multiples of 512.
   - **Simplest correct pacing:** K urbs of S bytes (e.g. 4 x 64 KB) queued on bulk OUT; `recv_urb` returns 0 and only pushes the urb to a ring; the main loop writes the data and hands the urb back with interrupts masked: `88a08f08(urb)`; if the endpoint ran dry (`[[urb+8]+0xc]==0`) kick it: `88a094aa(ep)` (with no active urb it only moves the queue head to active) [L], then, only if RxPktRdy is set (`W[0x200d0106+0x10n]` bit 0), `88a0ba08(n)` to service the parked packet [L]; without that check it fakes a ZLP completion [L]. The host is held back for as long as the main loop keeps all K urbs.
   - Confidence: high that the stack parks and never resumes (direct reading); medium that the host sees plain NAKs and that the kick is enough (hardware behaviour, plus two interrupt-path routines called from the main loop). Not settled without the probe.
3. **ZLP, short packets, stalls, class requests.** IN: a short last packet is automatic; a ZLP is sent only if `+0x2c` is set (stock `cdc_write` sets it when length % max packet == 0) [L]; split a long IN phase into urbs of whole packets, flag only the last. OUT: completion status 5; a ZLP completes with length 0 [L]. Class and vendor requests reach `recv_setup` in the interrupt with the setup at `+0x10` and a 0x280-byte buffer at `+0x18`; set `+0x20` to the reply length, return 0 (nonzero = EP0 stall); an OUT data stage (Cancel 0x64, 6 bytes) arrives before the call [L]. So Cancel, Get Device Status (0x67) and Device Reset (0x66) are plain `recv_setup` cases. Standard requests (all GET_DESCRIPTOR, incl. strings) stay in the stack: no MS OS 0xEE string, so use interface class 06/01/01 [I]. CLEAR_FEATURE(HALT) is done by the stack (`88a08a6c`, then event 8/9); its clear found no data-toggle reset [L]. Do not stall bulk endpoints: `88a08a6c` also resets the EP0 urb, so it belongs in setup context only [L].
4. **Composite: must switch.** One slot, and the stock start(0x400) registers CDC itself (`88a1327e`) and fails if the slot is taken [L]. A composite would mean our driver re-implementing CDC ACM plus an IAD (only placeable as an "extra" of the previous interface) [I]: not worth it. Enter and leave MTP by stop, unregister, register, start (a re-enumeration), as the serial service does today.
5. **Entry points** (all [L]; mask the USB interrupts around every main-loop call: the stack's own queue code and its SET_CONFIGURATION malloc run unmasked in the interrupt [L/I]). Static reach of rows 1-8 together (plus the indirect TX start `88a0c0b0`): USB core/controller `88a08a6c..88a0c0b0` (for `88a0ba08` this includes the EP0 standard-request path, not taken for n != 0) and the stock heap (`88a135e4`, `88a13628` -> `88a2ec6c..88a2f110`, L2 `0803e324/e374`, abort `0803dfc4`, ROM `0402xxxx`); **no storage client (`889f*`), mmc, SPI flash, updater, peer link or FORBIDDEN_TARGETS** (`re/reach.py`, controller op table `0x11803d00` added by hand).

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

Notes [L]: the stack frees urbs that are queued or active on OUT at reconfigure, deconfigure (incl. suspend) and stop without a callback (`88a095a8`): forget them; held ones stay ours. Avoid endpoint numbers 5 (static 0x190-byte buffers in `88a0926c`) and 6 (async DMA in `88a0b434`); the ISR serves 1-11. Use stock MTP's `0x89`/`0x0a`/`0x83` (above 8: PIO, no DMA cache work).

## Earlier claims (HARDWARE.md "unverified")

Right: slot, register/unregister, ops order, endpoints `0x89`/`0x0a`/`0x83`, send = alloc/fill/submit/free as `cdc_write`, recycle `88a08f08`, start = register + `88a35044(0)`, the open risk. Wrong or incomplete: the driver words +8/+0xc are a devinfo record and a config-record array (not descriptors), +0x10/+0x14/+0x1c are filled by the stack (struct must be writable), +0x18 is the configuration count; "one packet per urb" holds only for the stack's own OUT urb; the callback return values, the ZLP flag and the "recycle does not re-arm" detail were missing. New: every stock driver (MTP, CDC, MassStorage) declares 64-byte bulk endpoints although start sets POWER.HSEnab (`88a0bffa`); high speed requires 512.

## Still unproven (probe 3.2-3.4 must show)

1. Enumeration with our driver and start(0); speed (POWER bit 4) and that 512-byte bulk endpoints are accepted by macOS.
2. GetDeviceInfo round trip; ZLP flag on a 512-multiple reply; Cancel/Get Device Status reach `recv_setup`.
3. Starvation: hold all K urbs for 1, 5, 20 s mid-upload; count RX callbacks and RxPktRdy while held (expect 0 and set), the host error (if any) and its timeout; then kick and check a 4 MB upload byte-exact (SHA-256) with the number of kicks.
4. Negative control: no kick -> the upload hangs (proves the kick is needed, or shows the hardware re-interrupts and it is not).
5. Throughput for 2-3 (K, S) choices with the main loop responsive; cable pull and host reset mid-upload: no double free, held urbs freed by us, suspend -> event 0xa.

## Rejected

Stock MTP driver (interrupt code that blocks on A). Copy into a RAM ring and recycle in the interrupt, like CDC: no flow control, data lost once the ring is full. Returning nonzero from `recv_urb` when busy: the stack re-queues and overwrites an ACKed packet. Bulk STALL for pacing: EP0 side effects, hosts treat it as an error. `88a08f24` on an OUT urb to re-arm: runs the TX path on an OUT endpoint. Software re-raise of the USB interrupt: needs the stock pending word patched. Composite with stock CDC: one slot.
