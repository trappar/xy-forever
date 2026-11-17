# 007: Stock code inventory

Research record, 2026-11-14; no device, no build. Call targets taken with the builder's own `call_targets()` from build 153's `module.asm`: **74 stock entry points** (the union of the `*_CALLS` groups plus `STATE_WRITE_ENTRY` `889fbb0c`; `889f9982` and `040201c4` are allowed but not called). **[C]** read from our code, **[L]** from the 1.2.1 listing (`re/bootloader.py`, `re/reach.py`), **[I]** inferred, **[U]** not verified.

**Rule (PLAN "Shape"):** a stock call is fine if it is a synchronous function doing one bounded thing. Crossings: (a) runs a stock feature/state machine, (b) we write stock data to steer it, (c) unbounded wait inside a stock call (esp. on the other processor), (d) stock interrupt handlers live while we run, (e) reliance on bootloader start-up state we cannot re-create.

## Answer

74 stock entry points: 56 plain driver calls, 18 cross (12 + 5 with an unbounded wait on the other processor, 1 stock feature). The other crossings
are not calls: stock data we write (b), stock ISRs live while we run (d), bootloader start-up we depend on (e). Verdicts per group in §1-2.

## 1-2. Inventory and verdicts (every call is made from our main loops unless noted)

| Group (CPU) | # | What | Verdict |
|---|---|---|---|
| Inter-processor link (A+B) | 23 | list/queue init, IRQ/DMA/pin setup (`88a30798`, `88a319fa`, `889f5010`, `11a04f78`...), request builders (`889f4e82`, `889f65a4`, `889f87d4`, `889f8872`), B submit `11a0060c`, A reply `889f6508`, cache `889f4f10`, close `889f562e`/`889f5e38` | 23 plain calls; B polls its own request with our bounds [C `peer_services.cpp` poll, `file_boot.cpp` `b_ask`]. The **composition** crosses (b)(d)(e), see below |
| Remote file client (B) | 12 | stock role-1 posix: mount `889f7692`, open `889f3b50`, read `889f2924`, lseek `889f297c`, close `889f285a`, unmount `889f67e8`, write `889f28b8`, mkdir `889f3320`; dev `mtp-test`: `889f366a`, `889f3516`, `889f41e6`, `889f439a` (+ `889f44f4`) | **12 cross (c)**: each ends in `889f869e`->`889f7b12`, which submits and spins on `B[req+4]` with no bound [L]. Users: `read_file`, `file_write`, `mtp-test` [C] |
| Storage executors (A) | 8 | provider register `889f3910`; class executors `889f76ca`, `889f6fea`, `889f6cd6`, `889f72e4`, `889f6b7c`, `889f6dca`; closedir `889f44f4` | 8 plain (one request, gated by `decide_*`, returns). Note: the read executor re-posts itself into the pending slot (`889f66fc`) to stream [L] |
| yaffs (A) | 11 | construct `889f963e`, mount/unmount `889fae2e`/`889fb02c`, open/read/write/close, opendir/readdir/closedir, eMMC shutdown `88a344a8` (dead branch: `activated_` never set [C]) | 11 plain. Caveat (c, local): the media read `88a345f4` retries a failed sector forever (loop `88a34648..7a`) [L]; write `88a3454a` likewise [I from its string] |
| Boot-partition writer (B) | 5 | `mmc_tools` writer `88a06a7e`, capacity `889f65f0`, select `889f683c`, sector reads `889f6a6a`/`889f6abe` | **5 cross**: the writer is a stock feature (a); all five wait on A via `889f869e`/`889f7b12` (c) [L] |
| USB (B) | 6 | start `88a35044(0x400)`, stop `88a34fec`, CDC callback setter `88a12c7c`, available/read/write `88a13328`/`88a1334a`/`88a132f6` | 6 plain (writes retried with our 200/500 ms bounds [C]); the stack itself runs in the USB ISR (d) |
| Screen (B) | 2 | DMA wait `889eed4c`, frame transfer `889efa6a` | 2 plain: the unbounded DMA wait is pre-checked by our `dma_idle(100)` [C] |
| Keys / PSoC (B) | 4 | poll `88a24922`, key-callback setter `88a245dc`, link teardown `88a35e82`, delay `88a3426a` | 3 plain; **poll crosses (a), mild**: it is the stock PSoC command handler (greets, restarts itself) [L strings]; bounds [U] |
| Reset (A+B) | 1 | stock RCU reset `11a03518` | plain; also called from our watchdog ISR (`guard_expired`, after `usb_stop`) [C] |
| Hand-off / ROM (A+B) | 2 | RAM loader `88a3477a` (-> ROM loader), cache-aware copy `88a346a6` | plain (one-way hand-off) |

**Totals: 56 plain driver calls, 18 cross** (12 + 5 with an unbounded wait on the other processor, 1 stock feature). The rest of the crossings are not calls:

- **(b) Stock data we write:** log sinks `0x8803d06c..+0xc` zeroed by both sessions, our sink + log level `0x11b00074` during INSTALL (`hook_logs`); A's pending slot `[0x8804f6e0]+4` taken and cleared, release byte cleared, reply fields `+0x10/+0x14/+0x20/+0x24` (we re-implement stock `889f65c2`) [C `peer_services.cpp`, `storage_server.cpp`]; SPI driver objects `[0x11b00060]`/`[0x11b00064]` (+0x8..+0x20, +0x34, +0x9e, +0xb4/b8) and SPI/GPIO MMRs in `master_setup`/`slave_setup` (a port of the stock open's prefix, reusing the bootloader's GPIO callback records); yaffs device media slots (deny thunks, on the device object we construct: a safety gate); stale `smc_dma_active` `0x11905cc0`; CDC callback `[0x880734c0]` detached (via its setter); our handler in the stock IRQ dispatcher for the guard.
- **(d) Stock ISRs live while we run:** B: SPI master + DMA (`889f4f5e`, `889f4fa2`, `889f585c`, L1 `11a00200`), pin callbacks `11a00664`/`11a00458`, USB `88a0bdd8` (smxUSBD + CDC ring `88a130f2`), display DMA [I], PSoC UART [U]. A: SPI slave + DMA (`889f4f5e`, `889f4fa2`, `889f5dc6`, `889f64d0`) answer ops 1/10/7 and queue requests; their static reach is link code plus the stock allocator (`88a2f584`->`88a2ed30`), no storage and no `889f7b12` [L reach], so A's ISRs never wait on B; whether they allocate at run time (heap shared with A's yaffs) is [U]. eMMC (RSI) `88a061d6` completes A's transfers.
- **(e) Bootloader start-up we depend on:** clocks/SDRAM, eMMC controller left open (`0x11907ab4`), SPI driver objects + GPIO callback table (`0x88a30650`), IRQ tables (`0x11b008d0`/`0x11b005d4`), PSoC link, display + framebuffer pointer `0x8803dc58`, key reply `0x8807ce7e` (PLAY at power-on), boot reason `0x88a35934..3c`. Read-only checks elsewhere (role `0x8807cac4`, allocator `0x88a2a4d0`, registry `0x8803f6b8`, `0x88a1f3bc`) are guards, not steering.

Detail: §3, §4 in [007-stock-inventory.detail.md](007-stock-inventory.detail.md)

## 5. Open questions

1. Make "the screen processor never waits on the storage processor without a time limit" a hard rule, done in M2 by replacing its stock file calls with our own timed requests? (Recommended; no new risk.)
2. Install: keep the stock boot-partition writer (proven, power-safe) and research running it on the storage processor so nothing waits blindly; or accept today's behaviour (if A stops mid-install the device freezes and needs a power cycle)?
3. MTP uploads: first try reading USB data ourselves (small, plain hardware access) instead of 002's two interrupt-path stock calls?
4. Storage (yaffs) stays stock for good? Owning it is the only option here that could corrupt user data.
5. Keep the stock link driver (wrapped) and decide on owning it only if link faults continue after M2?
