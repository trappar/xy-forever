# 030: The 186-003 freeze, switching inside a boot, and a composite device
2026-11-15, reading `linear121.ann.asm`. 186-003 froze B on its switch (no 1209:0001, no serial, menu frozen).
[L] listing, [C] our code, [I] inferred, [U] unknown. MTP by restart (rejected; its branch is only in the maintainer's archive).
## 1. What the switch does on the stock side
- **Our order** [C]: `usb_stop()` = stop(1) on the live serial service, register (`88a09d5c`), start(0).
- **stop(arg)** `88a34fec`: if the running byte `[0x88a35934]` is set, teardown `88a13772` (only while the init flag
  `[0x88a137a0]` is set: events 4/0xd/0xe/0xf to the slot driver, controller detach, descriptors freed through the
  slot `88a09714`, the five class unregisters keyed on the flags word `[0x88a137a4]`; CDC's `88a132d0` empties the
  slot). Then, **only for arg != 0**, `88a087b4` -> `88a0890e`: disable and **close GP timer 7**, the stack's tick
  (`88a32058`; SEC source uninstalled by `88a30864`). It is not a PHY step: records 002 and 029 called it "PHY". stop
  never clears the running byte [L].
- **start(flags)** `88a35044`: stop(0) if running (teardown, timer kept); flags word; `88a087a2` -> `88a08886(1)`:
  re-open timer 7 only if `B[0x118039dc]` is 0 (`88a31ee4(7, ..., tick 88a08866)`, **return value ignored**; it
  returns 4 when the timer's entry at `0x88a32198`+0xc still reads open, 0xa when the SEC install fails); class
  registration `88a13676`; attach `88a08d52` [L]. The controller init `88a0bf86` (through the L2 ops table at
  `0x11803d00` [I]) clears POWER.SoftConn, **waits 5 ticks with no bound** (`88a0bfec` -> `88a08816` -> `88a08824`,
  spinning on `[0x118039e0]`, which only timer 7's interrupt increments), then sets HSEnab and SoftConn (`88a0c012`).
- **Builds 139-150** switched with start(flags) alone: its internal stop passes 0, so timer 7 was never closed. REBOOT's
  stop(1) is always followed by a system reset (timer, SEC reset; the next boot opens timer 7 once) [L/C].
## 2. The freeze, candidates ranked
1. **Timer 7 closed and re-opened in one boot (strong).** 186-003 was the first build ever to do that. If the re-open
   does not tick (stale open entry, SEC re-install `11a01680` -> `88a33d44`, SEC state: which one is [U]), the 5-tick
   wait spins forever in B's menu-loop pass before SoftConn: no USB device at all, no serial, frozen screen, exactly
   run-002 [I]. No masked wait found on the start path (our calls are unmasked; the CLI at `88a0c0bc` is TX start) [L].
2. **Our driver's descriptors** (`88a096c4` -> `88a0acc0`), first built on the device: a fault or loop looks the same
   [U]; lower (model and review 029 checked the layout). 3. The CDC teardown: every boot's start(0x400) runs it.
The cause cannot be settled by reading; option (a) below changes only candidate 1's step and so tells them apart.
## 3. (a) Switching function drivers inside a boot without the timer step: feasible
- **Enter MTP:** stop(**0**) (teardown with CDC still in the slot, CDC unregistered by `88a132d0`, timer kept) ->
  register ours (`88a09d5c`, slot empty) -> start(0) (its stop(0) skips the teardown, init flag 0; timer kept; the
  controller init does the soft disconnect / reconnect itself, so the Mac sees a new device).
- **Back to serial:** stop(0) (teardown through our driver; flags 0: no class unregister, so ours stays) ->
  unregister ours (`88a09d44`) -> free the urbs we hold (`88a09418`) -> start(0x400) (CDC registers, attach).
- **Addresses:** the eight already allowlisted plus start/stop (product calls; stop(0) as start calls it at `88a3505e`).
  Nothing new; a separate SoftConn write is not needed (it would be a register write outside the stock calls).
- **Change from 186-003:** the argument of the two stops (`usb_stop()` in `probe_turn`, `Port.stop` in
  `mtp_usb_stock.cpp`), 1 -> 0. If it still freezes, candidate 2 is left.
## 4. (b) Serial and MTP at once, one configuration: possible in principle, not with the slot as we use it
- **The stock stack already composes** [L]: flags bit 14 makes the class records join one driver instead of the
  slot. CDC `88a1327e` then skips its register (sets its composite endpoint fields), and `88a0d55e` (bit 14, last in
  `88a13676`) builds one combined driver from bits 0, 10 (CDC), 6 and 7 (stock MTP, `88a1247a` -> `0x88a12534`) and
  registers it (`88a0d6d0`). The stock boot uses 0x4440 and 0x4401; **0x4480 (CDC + stock MTP) is never used by TE**.
- **b1, stock CDC + stock MTP:** start(0x4480): no new address. But it is the stock MTP (storage through its
  callback table `0x88073380` -> `0x11906c48`, as builds 136-154), not our driver [U: untested combination].
- **b2, our driver with serial inside it:** one slot, one driver: our Driver would declare CDC's two interfaces plus
  MTP, and serve CDC ACM itself (urbs through the same eight calls; the serial service moves off the stock CDC calls
  `88a12c7c 88a13328 88a1334a 88a132f6`). No stock address beyond the eight. Open: whether our tables can carry the
  CDC functional descriptors (`AltDesc.extra`) and an IAD, which macOS needs for CDC in a composite device; reading
  `88a0d55e` (its 0x20-byte device template via `88a0c4d8`) and the builder `88a0acc0` answers that [U].
## 5. Progress marks that survive a power cycle (design note)
- B's link to A does not use USB, so after USB belongs to MTP B can still write a small file through A's file engine
  (FILE_WRITE's requests: open/write/close builders, no new stock call) under `/yaffs2/xy-forever/tmp/`, one whole
  rewrite per step, A's window closed after each (`settle_begin`, not waiting, while MTP runs). A freeze leaves the
  window to A's own 10 s close. Branch `mtp-restart-rejected` has it (`marks_write`, layout in `mtp_boot.hpp`).
- Nothing else is cheap and persistent: SDRAM survives only a warm reset, A's RAM dies with the power cycle, SPI is
  never written. Each mark costs a few link requests in the menu loop [U: not timed].
## 6. Recommendation: (a) first
186-003 with stop(0) in both switches plus file marks (before register, after start(0), configured, first command); no
new address, and it separates candidates 1 and 2. Then (b2) as the product (serial stays up), after
reading `88a0d55e` for the IAD question.
