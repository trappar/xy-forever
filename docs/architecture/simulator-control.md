# XY Forever architecture: simulator control

Part of [XY Forever architecture](../ARCHITECTURE.md); the simulator itself: [simulator](simulator.md).

The control port (its own setting, never derived from the endpoint's: `--control`, 7342 by default; with `--port 0`, or a test's `Simulator`, a port the system picks; a host names both, `--sim HOST:PORT,CONTROL`) takes one line in and gives a JSON reply out. Tests: `tests/test_simulator.py`.

## Status

`status` reports A's `partition`; `boot_writes`, yaffs changes on a boot partition; `rw_open`/`unclean_mounts`,
read-write mounts never unmounted; `to_a`/`to_b`, link messages relayed; `held`, `steps`, `work`, `idle`,
`flight`: the clock's state; `pid`: the board's (`Simulator.start` moves to another port pair when the answer is
not its own simulator's).

## Power, cable and resets

`power [play|plain]`, `cut`; `unplug` (the cable out and in: the connection closes and the bus-reset count that
ends #65's trust moves); `reset a|b`; `set a_boot_ms|b_boot_ms|a_redeliver|boot_a_window_ms|a_stall_now`.

## Faults

`set NAME N` with:

- **Link:** `a_silent` (status `a_silenced`: B's class requests kept from A or A's answers to them dropped),
  `a_slow_ms`, `drop_request`, `drop_body`, `cable_pull` (bytes), `stall_replies` (while set, no reply leaves B, as
  an IN urb that never completes: #314).
- **Storage** (storage operations, then a power cut and a PLAY power-on): `space`, `fail_write` (the Nth write,
  boot partition too), `fail_mount`, `vanish_marker`, `cut_after`, `fail_select` (switches, then fails),
  `fail_unmount` (while set), `fail_open` (A's), `full_path` (240 B, no NUL).
- **Delays**, in device time: `mount_ms`, `rw_mount_ms`, `unmount_ms`, `a_stall_now`.
- **Ask before USB control:** `set ask 1` (or `--ask`: #65's setting as settings.txt says; else it reads Off, and
  hosts here wait on it at most `XYF_ASK_LIMIT_S`).
- **Mod builds:** `set hold_mod_verify 1` holds a USB mod build at verification after it writes its output parts.
  The menu loop continues to answer CDC requests, including status and cancel, and cancellation still removes
  the output. `set hold_mod_verify 0` lets verification proceed.

## Stock calls and A's requests

`swap PATH` (stock path buffer); `inject CLASS OP ARG0 ARG2 [HEX]` sends A a raw request past B's checks (class
0: generic), `injected TAG` reads A's answer (`Simulator.ask_a`).

## Device time

`advance MS` moves device time ahead, so a device time-out (A's idle window, a held window) passes unwaited
(`Simulator.advance`: in the board's idle steps, answered once there and idle again); `settle` answers once the
clock stands still (`Simulator.settle`); `jump MS` moves it at once whatever runs (a slow pass,
`Simulator.jump`). `set hold_clock 1`: only `advance`, `jump` and board events move it, not B's waits or work (a test
that acts while B waits on device time: the wait never ends at the host's pace).

## Keys

`knob ID DELTA [COUNT]`, `knob_raw HEX [COUNT]`, `knob_jump N` (after the frames sent before it), `realkey K P`.
