# 021: Why `restart-a` froze the device on build 160

Research record, 2026-11-14, on build 160 (candidate `d1be9727…`); no device action. Evidence: device pass 5 (steps 32-37, what the screen showed, a status read
after the power cycle; the maintainer's, not shared). Tags: **[C]** our code, **[L]** 1.2.1 listing (`linear121.ann.asm`), **[M]** measured from the evidence,
**[F]** earlier finding (HARDWARE, HISTORY), **[I]** inferred, **[U]** unknown.

## 1. The sequence after `diag.restart_a`

- B: `t_test_restart_a` -> `menu_link::usb_command(restart_a_item)` -> `b_ask`, which waits for A's `command_ack`; then B replies ok [C]. The host got ok at
  87.51 s [M].
- A: `restart_a_item` takes the same branch as REBOOT's `reboot_item` (`file_boot.cpp` 536: `a_answer_out`, `storage_server::finish`, `a_close_files`,
  `reset_self` -> `11a03518`) [C]. That path works on the device for every REBOOT [M, earlier passes].
- Stock reset `11a03518` [L]: CLI; boot-reason mailbox (`11a03318`: `0xdeadf00d`, reason); RCU `SIDIS` bit 4 set/clear/set; `RCU_CTL` bit 8; SEC on, source 3 =
  fault (`SCTL3` 6), `FCTL` 0x41; WDOG0 count `0x1312d00`, enable; spin. It is a watchdog fault reset with no wait for idle bus masters. A reset like this with
  USB running froze B (096-108) [F].
- B's loop (`key_ui` -> `xyf_boot::tick`), every 250 ms: an op-10 keepalive that A's stock ISR echoes in every mode, or every fourth a probe (tagged select)
  that only A's loop answers [C]. Lost: a keepalive unanswered for `2 * answer_ms` = 1 s; a probe after `probe_ms` = 4 s; A's bootloader answering a probe also
  fails it (result != `~value`, `fail(protocol)`) [C].
- F2 (`link_lost_restart`), in the same loop pass, before any redraw: crash 0 < 3, so marker `0xf02` (active|held|guard|menu|relink), `menu_release` (guard off,
  `usb_stop` `88a34fec`), `reset_self` (guard off again, `11a03518`) [C].
- Expected next: B's bootloader (`88a35668` -> `88a352aa` -> `88a34a00`): op 1, op 10, select 1, sector reads, select 0, op 7, all waiting with no bound. A's
  bootloader (`88a35b90`) serves them and boots on op 7. Both load XY Forever, and B goes home with crash 1 [L, 016].
- **Measured timing:** the step took 16.51 s. The status wait took 15.06 s and found no port at all, so `gone()` saw the port vanish **about 1.4 s after the
  ok** [M]. That is the keepalive path: a keepalive sent about 250 ms after the reset, unanswered for 1 s (1.25 s), then `usb_stop`. A probe would give 4.3 s or
  more, and B's guard count (`0x77359400`, about 20 s) much more [C+I]. So B found the loss and ran F2 up to `usb_stop` [I, high]. A's slave was not echoing
  1.25 s after its reset [I].
- **Where it stops:** the last frame drawn is from before `b_lost`, when `status()` returned "Storage link: OK" (`file_boot.cpp` 1063). F2 resets before the
  menu redraws [C]. So the picture is B parked after `usb_stop`, either (a) in or after `11a03518`, or (c) inside its own stock bootloader, which draws nothing
  that we know of [U].

## 2. The candidates

- **(a) B's reset did not complete: most likely (about 55%) [I].** F2 is the only B reset ever made while B's master still has a link request open: the
  keepalive that made it declare the loss [C]. Every reset that came back had none (REBOOT: the reboot item was answered and B waits 100 ms; 108: B hung between
  requests) [C/F]. Every lone B reset with a stalled request froze with its last frame on screen: 148/149, 150 ("Step 2: restarting." even from B's main loop),
  and 160 [F/M]. A stuck bus master already blocks this fault reset (USB, 096-108) [F]. Which part of B (SPI DMA, the `SIDIS` handshake) blocks it is [U].
- **(c) B reset, and its bootloader waits forever on A's bootloader: possible (about 35%) [I].** No unbounded wait on A's side is known: A's bootloader polls
  for op 7 with no timeout, so time alone is not the problem [L/F]. But B's reset toggles the link lines about 1.3 s after A's reset, possibly while A's slave
  is opening, which no REBOOT does (B follows 150 ms after A) [I]. A re-entered XY Forever on B is ruled out: it draws "Starting key link..." or the "Internal
  link lost" halt screen, and every wait before them is bounded; showing "Storage link: OK" needs the link up and `home_start`, which starts USB [C].
- **(b) B never found the loss: ruled out.** USB vanished in 1.4 s, which matches F2's keepalive bound. Only the guard could stop USB otherwise, and it needs
  about 20 s [C+M].
- **(d) A did not reset: ruled out.** Same branch and stock call as REBOOT [C]. A answered and then went quiet for 1 s or more, while A's XY Forever ISR would
  have echoed the keepalive [M/I].
- **(e) The guard: not the cause, and it could not rescue.** F2 disarms it twice before `11a03518` [C]. The stock reset reprograms WDOG0 for its own fault [L],
  so after a stuck reset, or inside the bootloader, nothing XY Forever armed is left [I]. A guard reset of B would itself be a reset with the request open.

## 3. What survived the power cycle

- Before restart-a [M]: crash restarts 0, guard kicks 62232, `boot_reason` 0x14, 10 reads; restart-a answered ok.
- After the power cycle [M]: `present` 0, crash restarts 0, kicks 0, `boot_reason` 0x14. 0x14 is `11a0332e`'s default when the mailbox has no magic, so it says
  nothing [L]. The marker bytes `7a1a0afa a2a3a8a9 01050000 00005040` are not "YXFR". `home_begin` returned before `marker_clear` (present 0) [C].
- The marker is a decayed SDRAM remnant: 22 of 128 bits differ from the last valid marker (0x702) and 23 from F2's 0xf02 [M/I]. It cannot show whether F2 wrote
  its marker, and nothing else survives a power cycle [I].

Detail: §4, §5 in [021-restart-a-freeze.detail.md](021-restart-a-freeze.detail.md)

## 6. Proposed fix (after E1/E2)

- Before any reset of B from home (F2, and the same in `guard_expired` and `home_reboot`), close the link first: B's master holds no open request at its reset.
- `b_settle` (bounded) and then `session.end()` (stock master close `889f562e`, plus `check_closed` on the driver's active request, queue and DMA) before
  `menu_release` and `reset_self`. Record what `check_closed` saw in the marker's spare bits so the next boot reports it.
- Stock calls: `889f562e`, `88a34fec` and `11a03518` are already in the build's allowlist and in today's code (`session.end()`, `xyf_build.py` 100 and 834;
  REBOOT) [C]. No new stock call. Whether `889f562e` cancels an active DMA request is [U]. If it does not, cancelling that request would need a new call, which
  needs the developer's explicit yes.
