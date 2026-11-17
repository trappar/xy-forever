# 016: Why the guard's reset does not come back (detail)

Detail of [016-guard-reset.md](016-guard-reset.md).

## 1. After the guard fires (153, `guard expiry-test`)

- B: `guard_expired` -> `say` (a no-op since 153: nothing sets `screen_on`) -> `usb_stop` -> `reset_self` -> stock reset `11a03518` [C]. The port vanished and
  never returned [M: no `port` line after the test in `33-expiry-test.txt`, `36-end-status.txt`].
- A is not reset. It is guarded (`a_guarded`, from step 02's GUARD item) and, because step 31 read the state slots (`command_item`), in RPC mode: every request
  goes to `storage_server::serve` [C; M step order].
- B's stock bootloader, as at power-on: the link open sends op 1 (expects `0xfedebeda`) and op 10 `0x12345678` (`889f5596`, `889f55fc`) [L], which A's slave ISR
  answers in every mode [F HARDWARE]; then `88a34a00` -> `88a07240` selects partition 1 (op `0x18`, `889f683c`, waiting in `889f7b12` with no bound) [L].
- A answers it: `decide()` has no op `0x18`, so it replies `-1` [C]. `88a07240` returns a failed select's value as the length; `88a34a00` takes `0xffffffff` as
  read, sends release op 7 (`889f7b56`, unbounded), runs the teardown and RAM-boots whatever is at `0x8a000000` [L; HARDWARE "Restart order"].
- Then [I]: `0x8a000000` holds the 153 `os.ldr` loaded at the last restart (step 28) over the tail of the dry run's OS copy (`hand_off`, step 20). Op 7 is the
  stock release: A's staged session agrees with nothing frozen, closes, leaves its loop uncommitted, disarms its guard (`a_guard_off`) and halts in
  `cold.cpp stop()`. B's re-entered XY Forever finds no session: its handshake or first command fails before `home_start`, so USB never starts and B halts on
  the "Internal link lost" screen (or B never reached the RAM boot [U]). Nothing is armed on either side, so only a power cycle returns [M].
- Root [C/L]: a B that restarts alone needs A's *bootloader* (it serves B's boot reads, then waits for op 7 with no timeout [L]), but A's XY Forever answers
  instead. HARDWARE already states "A must never answer a restarted B's boot requests: leave them and restart A"; the code applies it only in the MTP/run entry
  (build 139; `run_engine` since 155).

## 5. Proving it in the simulator

`sim/` gains three host-side models; nothing changes in `target/`. (1) **Bootloader stand-ins**, from the listing. B: handshake, select 1 (unbounded wait), a
refused select boots what `0x8a000000` holds, sector reads retried forever, select 2 then 0, op 7, boot. A: serves B's boot reads from `bootpart.bin` until op
7, no timeout, then boots. A request pending when A resets goes to A's bootloader; this is switchable, to show what depends on 108's behaviour. (2) **Lone
resets:** control `reset a|b` (SDRAM kept, the other runs on). (3) **WDOG0:** CTL/CNT/STAT in emulated memory, counted by the cycle clock (aware of `advance`).
On expiry with source 3 registered, `guard_expired` runs at the next host call; TEST_HANG's `for (;;) {}` is rewritten to a spin host call that checks it.
Tests: expiry-test in RPC mode, in image mode and with a blob frozen; `reset b`; `reset a` with only status polls; guard on with 60 s of status polls (no
restart); three resets in a row end on the halt screen. Every case keeps `boot_writes` 0 and `rw_open` 0.

## 6. Plan items and device test

- **Build (sim):** the models and tests above. Done when, on today's code, the RPC-mode expiry, `reset a` and 60 s idle cases fail and the image-mode expiry
  recovers (153, 154 and 108 reproduced).
- **Build (fix):** F1-F3, and `device_pass.py` in the order below. Done when the sim build's tests pass, the full suite and `xyf_build.py --verify` pass, and
  the call audit lists the same stock targets as 157.
- **Review:** independent, against SAFETY (no reply to raw requests outside install; window closed before every reset; no reset loop) and the shape rule. Done
  when there is no blocking finding.
- **Device (one session, at most one power cycle):** USB install, restart, home (routine). Then, in rising risk, stop at the first miss: (1) guard on, hold on,
  60 s of status polls only (F3, 154's case); (2) `guard expiry-test` right after a restart, before any read (108's path, now via F1: home, crash 1); (3) read a
  file, then `guard expiry-test` (153's case). End with guard off, hold off, keepalive. Each step should be back within 10 s; report a miss at once. F2's lone-A
  case is tested only in the simulator unless a dev command that restarts A is added.
