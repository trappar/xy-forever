# 029: Review of the MTP USB probe build
**Verdict: GO WITH CONDITIONS.** No path writes flash, storage, the boot partition, OTP or a companion, or resets B. Worst case:
a freeze, or a live menu with USB dead; a power cycle clears either. Conditions: `scripts/mtp_usb_probe.py --window 60`
(finding 5), one run, stop at the first unexpected result. Checked against `linear121.ann.asm` and the linked image
of build 186-001 (`xyf-mtp-usb` mode). [L] listing, [A] linked asm, [I] inferred, [U] unknown.
## Findings, by severity
1. **Medium, item 5: stop(1), then start, twice in one boot.** Teardown is guarded: init flag `[0x88a137a0]` is set at 88a1376c;
   88a13772 skips when it is 0 and clears it (88a13790). Start's own stop (88a3505e) passes 0: nothing is torn down twice [L].
   The class unregisters (88a0d6e4, 88a124d2, 88a0d0d0, 88a0e99e, 88a132d0) are keyed on the flags word `[0x88a137a4]`
   (88a13680): none runs after start(0); our unregister clears the slot. Free 88a09418 touches only the urb (pool slot by
   address, 88a09428..50), so freeing ring urbs after the stop is safe. FIFO allocation fails closed past 0x4000 (88a0af3c) [L].
   **New on hardware:** the PHY goes down (88a087b4 -> 88a0890e) and up (88a087a2 -> 88a08886(1)) in one boot; the switches of
   139-150 used start(flags), whose internal stop passes 0 (PHY stays up). If that fails, the smallest fix is stop(0) for both switches.
2. **Low, item 6: race at requeue.** While pump() hashes a ring urb (milliseconds), event 0xa or 6 can flush the endpoint; requeue
   then appends that old-generation urb to the stack's queue (mtp_usb.cpp:318-324). The stack owns it from then on (freed at the
   next flush or stop): nothing is freed twice. If SET_CONFIGURATION reallocates endpoint objects, recycle writes through a stale
   `urb->ep` [U]: RAM only. Fix: inside requeue's mask, free the urb when `config_gen != main_gen`.
3. **Low, functional.** SET_INTERFACE (7) also flushes (027 item 2) but on_event ignores it (mtp_usb.cpp:83-85); suspend raises
   0xa and resume (0xc) brings no SET_CONFIGURATION: OUT has no urbs and stalls to the window. Fix: treat 7 like 6; re-arm on 0xc.
4. **Low, cosmetic.** `EpDesc.dir` is 1 for IN (mtp_usb.cpp:27-29); the stock MTP records (driver 88a12534) hold 0x80. The stack
   ORs this byte into bEndpointAddress (88a0a758..60) [L]: harmless only because 0x89 and 0x83 are odd. Fix: 0x80.
5. **Low, item 8: host bounds.** Script worst case: appear 10 s, GetDeviceInfo/OpenSession/CloseSession 3 s each, sink 30 + 10 s:
   about 60 s, over the default 30 s window (usb_service.cpp:2608). Safe (the device leaves mid-sink): hence `--window 60`, the maximum.
6. **Info.** macOS ptpcamerad claims class 06/01/01: its CloseSession ends the probe 0.5 s later (usb_service.cpp:2607), its
   OpenSession makes ours answer 0x201e; safe, may need a rerun. The 300 ms switch still serves requests (2587); the script sends none.
## Answers
1. **Calls:** against product-186 the image adds exactly the eight, each only from its `mtp_usb` stub (8ab2ecd4..8ab2ed74,
   `JUMP (P2)`) [A]; other stock targets unchanged, no 889f*, 11a0*, updater or companion target added; 027's closure holds.
   `check_mtp_usb` (xyf_build.py:185, at 892) refuses all ten outside `xyf-mtp-usb`; verify links `mtp_usb` only there (test_mtp_usb.py:70).
2. **Register:** the stock code reads `W[0x200d0100+0x10n+6]` for an endpoint index from the table: a plain read at 88a0b080
   (stall status), read-modify-write at 88a0b0d8 and 88a0b498 (bit 0 cleared after the FIFO read; n = 10 in stock MTP
   sessions) [L]. The plain stock status read implies no read side effect [I]. Ours is one 16-bit load (8ab2ecc6); the image
   holds no other 0x200d constant, so nothing of ours writes the controller [A].
3. **Kicks:** requeue masks (CLI) around recycle, kick, rx_parked, kick_rx (8ab2df20) [A]; it runs only from configure() and
   pump(); callbacks only push to the ring (mtp_usb.cpp:78-107). kick only when `!ep->active`; kick_rx refuses all but n = 10
   (8ab2ed78) [A]; EP0 never reaches recv_urb (88a0ba74: n = 0 goes to 88a0b882) [L]. Masked time: appends (<= 6 nodes), <= 512 B
   PIO (endpoint above 8, 88a0b4d4, no DMA wait), one completion; <= 8 frees; one malloc; one-packet replies (DMA 88a0b56e > 8 packets).
4. **Way back:** CloseSession -> 0.5 s -> leave -> start(0x400): serial. No host, stalled host, bus reset, cable pull (0xa:
   free_ours) end at the window: `xyf::clock::ms` (CYCLES, xyf_clock.hpp:24), checked every home_pump pass (key_ui.cpp:517 ->
   usb_service.cpp:2713), independent of USB; stop and start(0x400) need no host. Register refused: serial at once (2591); if
   CDC still held the slot, start(0x400) fails (88a35098): menu live, USB dead. A failed start(0) is unchecked: at the window
   stop(1) tears nothing down (flag 0), unregister, start(0x400): serial. start(0x400) failing: menu live, USB dead, result lost.
   Menu boot/restart: menu_release -> leave (2849). Nothing resets B: no reset_self, guard refused (2556), A ignores silence (022).
5. See finding 1. **6. Memory:** every heap call of ours is masked: alloc (mtp_usb.cpp:142-144, 240-242), free (114-117, 247-251). The stack frees
   IN urbs (`on_urb_sent` returns 1) and queued OUT urbs (flush); ours live only in the ring and are freed after the stop (295).
   88a08f4e pops a new active urb after recv_urb returns (88a08f98): a ringed urb is never active [L]. Slot after leaving: 2574, script checks 0.
7. **Spans:** probe_start (2558) runs identity(), engine_identity() (mtp_spans 88a08814+0xe00 and 88a0967c+0x24a4 cover all
   eight entries) and probe_spans() (88a0c0b0+0x80) before the stage is set; a mismatch answers `s_identity`, serial untouched.
8. **Host:** libusb only on 1209:0001 (found by VID:PID). Serial via `xyf_host.connect`, which sends HELLO to the first
   `/dev/cu.usbmodem*` (pre-existing: unplug other CDC devices); the probe request only after a HELLO naming `mode=mtp-usb`.
   Only `pkill -x ptpcamerad`: its launchd job stays and launchd restarts it on demand. Bounds: finding 5.
## Watch during the run
- 1209:0001 within ~1-2 s; serial back by window + 1 s; not back 15 s later: stop, power cycle, no retry. No MTP but serial back: PHY step (1).
- Result: `end` 2 (1 = window, 3 = refused), `slot_after` 0, `kicks` and `kick_rx` above 0, `urbs_dropped` 0, SHA-256 equal,
  `max_gap_ms` at most 250; note `usb.speed` (512-byte bulk needs high speed). `deconfigures`/`resets_bus` above 0 or an
  OpenSession 0x201e mean ptpcamerad or a suspend (findings 3, 6), not a fault.
**Addendum 2026-11-15.** The device refused the start with `identity`: probe_start re-ran identity() with the serial service live. A read-only
census (the maintainer's, not shared) found five words, all 0 in the image, in usb_spans #65 and #86 only (0x88a134ac/b0/b8,
0x88a137a0, 0x88a321a4); engine_identity() and probe_spans() pass. Rule: the MTP start skips that second identity() run; 186-003's start uses
the menu-start identity() result (menu_usb 2, usb_off 0), then the MTP spans (`mtp_usb_gate.hpp`); spans, allowlist and product unchanged.
