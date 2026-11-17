# 007: Stock code inventory (detail)

Detail of [007-stock-inventory.md](007-stock-inventory.md).

## 3. Options per group (cost, risk)

- **Link:** keep + wrap (today: B's own requests are bounded). Own = SPI master/slave + DMA on both CPUs, own framing/CRC/timeouts: ~600-1000 lines, framing
  host-testable, timing device-only, no data risk; must leave SPI as the stock OS's init expects at hand-off [I]; no open driver fits (ADI HRM only).
- **Remote file client (B):** own, cheap: route reads/writes through `Stock121PeerSession` submit+poll or our command/blob items (~200 lines,
  simulator-testable, A side unchanged). This removes 12 of the 18 crossings.
- **Executors + yaffs (A):** keep; yaffs is a library called synchronously. Own = yaffs2 core (~15k lines; GPL or Aleph One licence) + the undocumented TE eMMC
  tag layout (`yaffs_te_drv`) + RSI host driver; must match the stock on-disk format bit for bit; only a host image test is possible offline; a mistake corrupts
  the user's projects. Do not own.
- **Boot-partition writer:** keep (commit-last A/B, power-safe; SAFETY names it the only boot-partition writer). Wrap option [I]: run it on A, where its helpers
  take the role-0 branch to the local `88a08xxx` sector code [L role checks in `889f683c`/`889f6a6a`/`889f6abe`/`889f65f0`], so nothing waits on the link.
  Owning it is brick-adjacent: no.
- **USB:** keep the stack as a driver. Own fully = MUSB device + EP0 + CDC + MTP, ~1500-2500 lines, enumeration device-only, no data risk; TinyUSB's MIT
  `mentor/musb` port may fit [U]; Linux musb is GPL.
- **Screen, keys, reset, hand-off:** keep. Screen ~200 lines to own, low value; PSoC protocol undocumented; watchdog already ours; the ROM loader is the boot
  path.

## 4. Ranked crossings (most dangerous first)

1. **B waits on A with no bound** (17 entries above). If A stalls (media retry, fault, restart), B freezes with USB up and needs a power cycle; the guard is
   disarmed during `file_write` and `mtp-test` [C `usb_service.cpp` 502, 654] and set to its maximum during INSTALL (690). Fix: **M2**: B's file path uses only
   bounded submit+poll; delete `CLIENT_CALLS`, `FILE_WRITE_CALLS`, `MTP_TEST_CALLS`. Install: new research item (before #14): run `88a06a7e` on A, or keep it on
   B with A-side progress reports and a bound.
2. **Link composition** (b)(d)(e): our port of the stock SPI open, the pending-slot takeover and stock ISRs doing the protocol; past faults lived here (069
   dispatch, 146 request/body split). Fix: keep + wrap now (single submitter: B's main loop; invariants in `test_peer_dispatch`); new item after M2: decide on
   owning the link if faults persist.
3. **002's kick** (`88a094aa`, `88a0ba08` from the main loop, USB IRQ masked): crosses (a): it runs interrupt-path stack routines outside the ISR, and
   `88a0ba08` calls our `recv_urb` there. Alternative [I]: keep the stack's single bulk-OUT urb held so its ISR never touches that FIFO (002: returns when
   `[ep+0xc]==0`) and read RxCount/FIFO/clear RxPktRdy ourselves (~60 lines, per-endpoint MUSB registers). **M3 (#9):** probe both; prefer our own read.
4. **yaffs media retry forever** (`88a345f4`): cannot be wrapped from outside; item 1 makes B notice; keep A's guard armed for long jobs (**M2**).
5. **A's slave ISR and the stock allocator** [U]: check run-time allocation in **the file engine (009)**.
6. **PSoC poll** (a, mild) and the small (b) patches (log sinks, `smc_dma_active`, CDC callback): keep; the INSTALL log hook goes if item 1 moves the writer.
