# 016: Why the guard's reset does not come back

Research record, 2026-11-14; no device, no build, simulator not run. **[C]** our code, **[L]** 1.2.1 listing (`linear121.ann.asm`, `fn.py`), **[M]** device evidence (device passes 1-2 and 3, builds 153-156; the maintainer's, not shared), **[F]** commit message or earlier doc, **[I]** inferred, **[U]** unknown.

## 1. After the guard fires (153, `guard expiry-test`)

- B: `guard_expired` -> `say` (a no-op since 153: nothing sets `screen_on`) -> `usb_stop` -> `reset_self` -> stock reset `11a03518` [C]. The port vanished and never returned [M: no `port` line after the test in `33-expiry-test.txt`, `36-end-status.txt`].
- A is not reset. It is guarded (`a_guarded`, from step 02's GUARD item) and, because step 31 read the state slots (`command_item`), in RPC mode: every request goes to `storage_server::serve` [C; M step order].
- B's stock bootloader, as at power-on: the link open sends op 1 (expects `0xfedebeda`) and op 10 `0x12345678` (`889f5596`, `889f55fc`) [L], which A's slave ISR answers in every mode [F HARDWARE]; then `88a34a00` -> `88a07240` selects partition 1 (op `0x18`, `889f683c`, waiting in `889f7b12` with no bound) [L].
- A answers it: `decide()` has no op `0x18`, so it replies `-1` [C]. `88a07240` returns a failed select's value as the length; `88a34a00` takes `0xffffffff` as read, sends release op 7 (`889f7b56`, unbounded), runs the teardown and RAM-boots whatever is at `0x8a000000` [L; HARDWARE "Restart order"].
- Then [I]: `0x8a000000` holds the 153 `os.ldr` loaded at the last restart (step 28) over the tail of the dry run's OS copy (`hand_off`, step 20). Op 7 is the stock release: A's staged session agrees with nothing frozen, closes, leaves its loop uncommitted, disarms its guard (`a_guard_off`) and halts in `cold.cpp stop()`. B's re-entered XY Forever finds no session: its handshake or first command fails before `home_start`, so USB never starts and B halts on the "Internal link lost" screen (or B never reached the RAM boot [U]). Nothing is armed on either side, so only a power cycle returns [M].
- Root [C/L]: a B that restarts alone needs A's *bootloader* (it serves B's boot reads, then waits for op 7 with no timeout [L]), but A's XY Forever answers instead. HARDWARE already states "A must never answer a restarted B's boot requests: leave them and restart A"; the code applies it only in the MTP/run entry (build 139; `run_engine` since 155).

## 2. Why 108 recovered and 153 did not

- 108 [F HARDWARE]: A was in image mode with nothing frozen, so `peer_boot::Server::process` returned `waiting` and B's select stayed unanswered. A saw no command, request or response for 25 s (`a_alone_ms`, build 103) and ran the stock reset, then A's bootloader answered the request still in flight. That 108 ran before any file read is [I]; its sequence was not kept [U].
- Guard code since 108 changed only cosmetically [C, diff of builds 108..153]: 109 (arming writes the home marker), 112 (stuck-at text, role check before `usb_stop`), 151, 153 (removed the old service `run()`, the only setter of `screen_on`). `guard_arm/disarm`, `reset_self` and A's alone rule are the same logic.
- What changed is the condition: the device pass `scripts/device_pass.py` runs the guard stage last, after file reads and the MTP self-test, so A is in RPC mode (latched by `command_item`, `set_rpc`, since 065/066) and answers. 108's recovery was never general; 150's `--fault 5` likely failed the same way [I].

## 3. The 154 loss: A restarted alone

- The screen [C]: in 153+ the only path to screen 4 ("USB service") is `xy_forever_no_image()` (`file_boot.cpp`). B's home loop ends on `b_lost`, `menu_release` stops USB, no image is loaded, and B draws "USB service" / "Internal link lost. Turn the OP-XY off and on."; then both halt. The header is the old service screen reused.
- Trigger [C]: GUARD off (step 37) disarms B only; A keeps `a_guarded = 1` and its alone rule, which counts `commands_seen + served + requests + responses`. B's op-10 keepalives (tag `KA`, every 250 ms) reach only `observe()` (`activity_`), which the rule ignores; STATUS, HELLO, GUARD and HOLD never reach A. So 25 s after the last A-bound request, A runs the stock reset.
- Timing [M]: the last A-bound request is step 31's state read at 95.68 s; the session ended at 97.94 s (18:21:37.9 UTC); the keepalive log ends at 10:22:02 local, about 26 s after that read. That is 25 s plus B's 1 s keepalive patience (`tick`: busy > 2 x `answer_ms`) [C]. A's bootloader then waits for an op 7 with no timeout [L]; B has halted.
- 156 survived 6 min of keepalive-only traffic, and that fits [M device pass 3, conformance runs 1-2]. Its own keepalive ran only 14 s (10:41:03-10:41:17, under 25 s) before conformance run 1 sent reads (A-bound) and then REBOOTs (`device.reboot/*`, cases 23 and 36-38). A REBOOT carries `flags()` in the marker [C `home_reboot`]. With the guard off, the marker has no `marker_guard`, so the next boot never sends `guard_item` and A's `.data` starts with `a_guarded = 0`. After the reboots B reports `guard_kicks` 0 [M]. Between 154 and 156 the alone rule changed only by closing files first [C].
- The exact condition [C]: A stays armed from the first GUARD on (or a guard-carrying restart) until A itself restarts. GUARD off, HOLD off, STATUS and HELLO do not disarm it. 154 had no restart after its GUARD off; 156 had several. The device as it is now (156, after the conformance REBOOTs, keepalive only) is not at risk. To reproduce 154 (not run): GUARD on, optionally one file read, GUARD off, HOLD off, then only `status` polls for at least 30 s with no REBOOT. Expect USB to drop about 26 s after the last A-bound request, then the "Internal link lost" screen.
- Confidence: high (about 85%, unchanged). B's guard was really off (kicks stopped at 13543 [M]); there is no idle timer on B. It mirrors (1): one processor restarts alone and the other does not follow. Here A restarts falsely and B halts instead of restarting.

## 4. Fix (smallest; no new stock call)

Rule: a lone restart of either processor becomes a restart of both, in the stock order (A's bootloader waits for B; B's bootloader then loads both).
- **F1 (A) never answers B's bootloader; it follows.** In A's dispatch (`Stock121PeerSession::poll` snapshot, every route: RPC, image, run entry), "B restarted" is (a) an op-10 header carrying `0x12345678` after the first command (`observe()` reads that buffer), or (b) a raw-layer request (partition `0x18` with a non-command argument, sector `0x11`/`0x12`) while nothing is frozen, outside install mode. A then sends no reply, runs `a_close_files` (window first) and `reset_self`. A's bootloader answers the request in flight [F 108].
- **F2 (B) restarts on a lost link at home instead of halting.** On `b_lost` while `home`: marker `active|menu|flags` with crash + 1, `menu_release` (USB off), `reset_self`. At `max_crash_restarts` (3), B keeps today's halt screen. B's bootloader then meets A's bootloader (A restarted) or F1.
- **F3 (A): the alone rule counts keepalives** (`activity_`), so an idle host never restarts A while B is alive. Also, GUARD off sends A an item that clears `a_guarded`, so "guard off" means off on both processors (154's exact gap). F2 is what makes a lone A restart harmless anyway.
- Stock calls: only `11a03518` (reset) and `88a34fec` (USB stop), both already allowlisted and called today [C, 007], so no new call needs approval. Self-restart on link loss is new behaviour in the USB service and needs the developer's explicit yes (SAFETY, "Before installing" 5). Install mode keeps today's behaviour (B's writer uses the same ops); a lone B restart during an install is [U].

## 5. Proving it in the simulator

`sim/` gains three host-side models; nothing changes in `target/`. (1) **Bootloader stand-ins**, from the listing. B: handshake, select 1 (unbounded wait), a refused select boots what `0x8a000000` holds, sector reads retried forever, select 2 then 0, op 7, boot. A: serves B's boot reads from `bootpart.bin` until op 7, no timeout, then boots. A request pending when A resets goes to A's bootloader; this is switchable, to show what depends on 108's behaviour. (2) **Lone resets:** control `reset a|b` (SDRAM kept, the other runs on). (3) **WDOG0:** CTL/CNT/STAT in emulated memory, counted by the cycle clock (aware of `advance`). On expiry with source 3 registered, `guard_expired` runs at the next host call; TEST_HANG's `for (;;) {}` is rewritten to a spin host call that checks it. Tests: expiry-test in RPC mode, in image mode and with a blob frozen; `reset b`; `reset a` with only status polls; guard on with 60 s of status polls (no restart); three resets in a row end on the halt screen. Every case keeps `boot_writes` 0 and `rw_open` 0.

## 6. Plan items and device test

- **Build (sim):** the models and tests above. Done when, on today's code, the RPC-mode expiry, `reset a` and 60 s idle cases fail and the image-mode expiry recovers (153, 154 and 108 reproduced).
- **Build (fix):** F1-F3, and `device_pass.py` in the order below. Done when the sim build's tests pass, the full suite and `xyf_build.py --verify` pass, and the call audit lists the same stock targets as 157.
- **Review:** independent, against SAFETY (no reply to raw requests outside install; window closed before every reset; no reset loop) and the shape rule. Done when there is no blocking finding.
- **Device (one session, at most one power cycle):** USB install, restart, home (routine). Then, in rising risk, stop at the first miss: (1) guard on, hold on, 60 s of status polls only (F3, 154's case); (2) `guard expiry-test` right after a restart, before any read (108's path, now via F1: home, crash 1); (3) read a file, then `guard expiry-test` (153's case). End with guard off, hold off, keepalive. Each step should be back within 10 s; report a miss at once. F2's lone-A case is tested only in the simulator unless a dev command that restarts A is added.

## 7. Open questions (answered in §8)

1. When one processor is lost at the home menu, may XY Forever restart both by itself (up to 3 times, then today's "Internal link lost" screen)?
2. What did the screen show after 153's expiry test, and did 154's screen say "Internal link lost"? Either answer confirms or rules out §1 and §3.
3. Add a dev command that restarts A alone, to test F2 on the device, or keep that case in the simulator only?

## 8. Built (build 159)

F1-F3 as §4, plus: A also follows (storage closed, restart) when B is silent past its 10 s idle window after B spoke, since that window always ends before the 25 s rule (otherwise A halted); B sends a probe every fourth keepalive that only A's loop answers, so a lone A restart is found even when A's bootloader echoes keepalives; B's restarts share the crash count (a lost-link restart may be the third), refused once the boot-partition writer ran. Questions 1 and 3: yes (both restart by themselves, at most 3 times; a dev command may restart A alone for F2's device test). A held request reaching A's bootloader stays the one dependency on Test 108 (`tests/test_guard_reset.py`).
