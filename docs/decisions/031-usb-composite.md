# 031: Why our driver never enumerated, and serial + MTP as one composite device
2026-11-15, listing + linked image of build 186-005. [L] listing, [A] image, [C] ours, [U] unknown, [E] device test.
## 1. Nothing on the bus after our start(0): our config record is 8 bytes short
- **A stock config record is 0x18 bytes** [L]: +0 name, +4 bmAttributes, +5 bMaxPower, +8 n_ifaces, +0xc ifaces,
  **+0x10 n_iads (at most 4), +0x14 IAD array** (0xc each: name, class, subclass, protocol, first interface, count).
  `88a0a992` refuses n_iads > 4 (`88a0aa0e`) and builds IADs from +0x14 (`88a0aa34..98`); stock records end in two zero
  words (CDC `0x88a13460`, MTP `0x88a12504`). Record 002's 0x10 layout was wrong.
- **186-005** [A]: `kConfig` (0x10 bytes, `8ab34e34`) is followed by `kIfaceDesc`, so n_iads reads the "MTP" pointer
  `0x8ab34ea8`: `88a0a992` 0 -> `88a0acc0` -1 -> `88a096c4` 0 -> attach `88a08d52` frees its device, -1 -> `88a13714` 0
  ("smxUSBD initialization failed", `88a35098`); init flag `[0x88a137a0]` stays 0. The mock never read +0x10/+0x14.
- **Why silent** [L]: the controller init `88a0bf86` (run by attach before the descriptors) already set SoftConn
  (`88a0c012`), but USB SEC sources 0x4d/0x4e are enabled only by event 2 (`88a0c48c` -> `88a08cb2` -> op +0x2c
  `88a0c3d8` -> `88a08804`), sent after a good build only. Connected but deaf: no SETUP is ever served. Not the switch.
- **For ours, registered before the boot's only start** [L]: empty slot (no start(0x400) before); start flags with no
  class bit (0, 6, 7, 10, 14: `88a13676` returns 1); config records 0x18, n_iads <= 4; one endpoint number per endpoint
  (below). The first start opens timer 7 (`88a08886`); iSerial becomes the device's own (`88a34edc`, `88a0ad74`).
- **2. Way back**: init flag 0, so no teardown; start(0x400) re-ran attach on a deaf controller; spin not found [U]. Moot.
## 3. Composite: b1 is out, b2 needs start(0x200)
- **b1 `start(0x4480)`** [L]: `88a0d55e` copies stock interface records unchanged: CDC 0x81/0x0a/0x89 (`0x88a134bc`,
  `0x88a134f0`), stock MTP 0x89/0x0a/0x83 (`0x88a1262c`). Two functions on 0x89 and 0x0a, and the FIFO is allocated
  once per endpoint number (`B[0x11803cb0+n]`, `88a0c202`): broken by construction; and it is the stock MTP (002).
- **b2, flags 0x200** [L]: bit 9 registers no class (`88a13676`; no class unregister keys on it) but makes the stack
  build and fill CDC functional descriptors (type 0x24) for interfaces 0..1 (`W[0x88a0d708]` = 0, `88a1368c`): size
  `88a0a03a` (table `0x11803b30`), fill `88a0a3e0` -> `0x11803c5c`. At flags 0 they come out with length 0 (`88a0a056`).
  Extras = array of 0x3c-byte records (`88a0a22a`): +0 type 0x24, +1 subtype, +4/+5: header (0; bcdCDC set to 0x0200),
  call management (1: caps +4, data interface +5), ACM (2: caps +4 = 0x02 as stock), union (6: master +4, slave +5).
  The serializer emits each IAD just before its first interface (`88a09834..62`). Variants:

| | b1 start(0x4480) | b2 start(0x200), our driver |
|---|---|---|
| Stock addresses | none new, stock MTP class | none beyond the eight + start/stop; new flag value 0x200 |
| Our code | small | ConfigDesc 0x18 + IAD, extras, CDC requests, serial transport on our endpoints |
| Fails how | endpoint clash (certain) | [E] macOS binding; bit-9 descriptors as read |
- **b2 layout**: DevInfo class EF/02/01; IAD {first 0, count 2, 02/02/01}; interface 0 CDC comm 02/02/01, 4 extras,
  notify IN 0x81 interrupt 16 B interval 0x10 (as stock); interface 1 CDC data 0a/00/00, bulk OUT 0x08 + bulk IN 0x8b,
  512; interface 2 MTP 06/01/01 on 0x89/0x0a/0x83 (as tested). Numbers 1, 3, 8, 9, 10, 11 (5 and 6 avoided, 002;
  0x08 OUT is the stock MSC's, `0x88a0eae8`). FIFO 0x40..0x4000 (`88a0bf92`, `88a0af3c`), 1 KiB per 512-byte endpoint:
  about 4.2 of 16 KiB. VID:PID 1209:0001 for tests; the shipping pair is undecided (stock uses 2367:0021).
## 4. Serial service on our driver (b2)
- Today [C]: `cdc_available 88a13328`, `cdc_read 88a1334a`, `cdc_write 88a132f6`, callback `88a12c7c`: unused on b2.
- `recv_setup` by wIndex: 0 -> SET_LINE_CODING 0x20 (7-byte data stage already in; store), GET_LINE_CODING 0x21 (7 bytes,
  default 115200 8N1), SET_CONTROL_LINE_STATE 0x22, SEND_BREAK 0x23 (ack); 2 -> MTP 0x64/0x66/0x67; else stall.
- Bulk OUT 0x08: two 512-byte urbs; `recv_urb` copies into a RAM ring and returns nonzero (stack re-queues, as stock CDC)
  while 512 bytes fit, else holds the urb and pump() returns it with `88a08f08` + kicks (MTP's path). Bulk IN 0x8b:
  alloc `88a092ee`, copy, submit `88a08f24`, ZLP on 512 multiples. Parser reads the ring; dispatch by `urb->ep`.
## 5. First device tests
- **T1 (confirms 1)**: 186-005 + ConfigDesc 0x18 (n_iads 0): expect 1209:0001, GetDeviceInfo; way back may freeze.
- **T2 (composite)**, cheap net: the normal build writes `xy-forever/tmp/composite.flag` and does the post-install
  REBOOT (A acks the reboot item and resets, B stops USB 100 ms later and resets: both processors, 022). That boot
  deletes the flag FIRST, then registers ours and starts 0x200; no valid HELLO in 20 s -> the same REBOOT from home
  (`usb_command(reboot_item)`), next boot stock serial. User sees the menu twice, ~20 s apart. Marks as in -005: after
  start, first SETUP, configured, first HELLO. Never back: power cycle; dev menu last.
## 6. Disadvantages of always-on
macOS ptpcamerad opens MTP at every plug-in (Photos may pop up); a new VID:PID means a new Web Serial grant and port name.
Windows/Linux bind via the IAD (gvfs takes MTP). Nothing reaches the OS (menu_release stops USB before every boot).
## Recommendation: b2, one builder per step
1. T1. 2. `mtp_usb`: ConfigDesc 0x18 + IAD + extras + DevInfo EF/02/01 (host test serializes as the stack does). 3. CDC
requests, serial ring and writes on our endpoints (transport switch in usb_service). 4. Boot: register + start(0x200),
flag/timeout net, marks. 5. T2.
**Needs the developer's explicit yes:** start with flag 0x200 (a stock call, a value TE never uses); the shipping VID:PID.
