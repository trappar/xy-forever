# 021: Why `restart-a` froze the device on build 160 (detail)

Detail of [021-restart-a-freeze.md](021-restart-a-freeze.md).

## 4. What the simulator gets wrong

- **The wrong assumption:** a processor's stock reset always completes, whatever its link is doing. `sim/stock.cpp` makes `11a03518` an
  `exit_processor(exit_reset)`, and `sim/runtime.cpp` `start()` drops B's in-flight messages (`if (which == 1) { outstanding.clear(); return; }`). In-flight
  frames also vanish without a trace at either end (`forward()`: "nobody on the link"). On 160 the simulator's restart-a also resets B with the unanswered
  keepalive open: A's 300 ms start loses it [C, sim log in the worktree run].
- **Change:** the board records "B has a link message with no reply". When B calls `11a03518` with one open (or with USB on), B halts off USB and never reaches
  its bootloader (log "stock reset with a link request open: B frozen"). Make it a switch like `a_redeliver`, default on. Then `test_restart_a_command` and the
  spread test fail on today's code [I]. REBOOT, 108 and the conformance REBOOTs still pass, because none has a request open; that consistency check backs (a).
  For (c), a second switch: A's bootloader ignores the first frame after a B reset that lands within N ms of its slave opening.

## 5. Device experiments

- **E1 (no risk, routine):** one REBOOT to the menu (proven safe) while the developer watches the screen. Question: does the OP-XY logo appear before the menu
  returns? The bootloader expects SPIBOOT to have set up the display (`88a1d9c2`, "display timestamp from SPIBOOT") [L]. If a soft reset brings the logo back,
  160's frozen menu frame means B never got through its reset: (a). If not, HARDWARE's "keeps its last frame" holds and the screen cannot tell (a) from (c).
- **E2 (cheapest test that tells them apart: at most one power cycle, the developer looks at the screen for 10 s):** build 161 = 160 plus the fix below, then
  one `restart-a`. Home again with crash 1 confirms (a) and the fix. The same freeze points to (c); then power cycle with PLAY. No experiment tells (a) from (c)
  without a B reset after a lone A restart, and that reset is the step that froze [I].
