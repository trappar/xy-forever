# XY Forever architecture: simulator

Part of [XY Forever architecture](../ARCHITECTURE.md).

## Simulator (offline)

The device's own C++ on the Mac, speaking the protocol on 127.0.0.1 (raw byte stream, no
framing of its own). Code reaches the device only after it passes here (ORCHESTRATION.md).

- **Hosted** (compiled from `target/`, rewritten mechanically by `xy_forever/sim.py`: casts through emulated
  memory, `stock<>` calls to `sim/stock.cpp`, asm to host calls; a rewrite that stops matching fails the build):
  `usb_service`, `usb_proto`, `storage_server`, `menu_link`, `file_boot` (both roles), `peer_services` (incl.
  `poll`), `cold_storage`, `retained_lookup`, `cold_heap`, the library/catalog/state modules, `native/` ldr, hash,
  peer_boot, trace_slot. Each processor is a process; the stock 1.2.1 image (private) is in its memory, so every
  identity CRC checks real bytes; SDRAM survives a processor reset.
- **Stood in for** (`sim/`): the stock calls (USB CDC = the TCP port, open only while USB is on, so a restart
  drops the client and the port comes back; the SPI link = a board relay, A's stock ISR answers generic ops and
  queues class requests in the pool slot; the stock request builders B's client uses; A's executors; yaffs = a
  host folder with read-only/read-write mounts and media gates; the boot partition = a file, two slots, header
  last, the other erased even on verify failure; model encryption below), the retained cold state, `cold.cpp` (its policy is extracted; an OS
  start stops both processes: `os`; no image: `halted`) and `key_ui` (the menu loop without screen or keys:
  `home_pump`, tick, link check; SCREEN shows a test pattern, KEY does nothing).
- **Device time** (`sim/clock.cpp`): interactive runs (`python3 -m xy_forever.sim`, the web app, `conformance.py
  --sim`) use real time. Test runs (`xy_forever.sim.Simulator`, `--virtual-clock`; the TypeScript and web tests
  too) use a virtual clock that only the simulator moves: the board steps it (1 ms after activity, doubling to 8
  ms, never past a known event: a reset delay, a delayed link message, a fault's delay) only while both
  processors are idle (no link message in flight, each main thread in a wait or polling the clock with nothing
  done); a relayed message and a busy processor's clock reads add their own time. A processor that is slow
  on a busy host stops the clock, so no device time-out depends on host load. Idle steps keep pace with host
  time (at most 1 s behind), so a host's pauses mean what they mean on the OP-XY; `advance` fast-forwards.
- **Resets** (`sim/boot.cpp`): a stock reset always lands in the bootloader stand-in (no WDOG0 model). B's probes A
  first: on only if A reset <= `boot_a_window_ms` (400; device 0.15-1.4 s) before B or later, else waits (`b_waits_a`).
- **Not hosted:** the menu's screen and keys, os.ldr itself, CDC ring and USB timing, NAND timing, real encryption,
  stage 2; yaffs is a (case-insensitive) host folder.
- **Run:** `python3 -m xy_forever.sim` (`SESSIONS/simulator/` in the private folder: seeded `yaffs2/`, `bootpart.bin`,
  `builds/`; `--port`, `--plain`, `--version V`, `--pre-v1`, `--virtual-clock`), then `xyf_host.py --sim 127.0.0.1:7341` (or `XYF_SIM`),
  `scripts/device_pass.py` / `scripts/conformance.py --sim …` (evidence removed when nothing failed, unless `--keep`).
- **Control** (port + 1, one line, JSON reply): `status` (A's `partition`; `boot_writes`, yaffs changes on a boot
  partition; `rw_open`/`unclean_mounts`, read-write mounts never unmounted; `to_a`/`to_b`, link messages relayed),
  `power [play|plain]`, `cut`, `set NAME N` with `a_silent` (status `a_silenced`: B's class requests kept from A or A's answers to them
  dropped), `a_slow_ms`, `drop_request`, `drop_body`, `cable_pull`
  (bytes), `space`, `fail_write` (the Nth write, boot partition too), `fail_mount`, `vanish_marker`, `cut_after`,
  `fail_select` (switches, then fails), `fail_unmount` (while set), `fail_open` (A's), `full_path` (240 B, no NUL)
  (storage operations, then a power cut and a PLAY power-on); `swap PATH` (stock path buffer); `inject CLASS OP ARG0
  ARG2 [HEX]` sends A a raw request past B's checks (class 0: generic), `injected TAG` reads A's answer
  (`Simulator.ask_a`); `advance MS` moves device time ahead, so a device time-out (A's idle window, a held window)
  passes unwaited (`Simulator.advance`: virtual, in the board's idle steps, answered once there and idle again;
  real time, one jump); `jump MS` moves it at once whatever runs (a slow pass, `Simulator.jump`); `knob ID DELTA
  [COUNT]`, `knob_raw HEX [COUNT]`, `knob_jump N` (after the frames sent before it), `realkey K P`; `reset a|b`,
  `set a_boot_ms|b_boot_ms|a_redeliver|boot_a_window_ms|a_stall_now`; fault delays (`mount_ms`, `rw_mount_ms`,
  `unmount_ms`, `a_stall_now`) are device time. Status `virtual`, `steps`, `work`, `idle`, `flight`: the clock's state; `pid`: the
  board's (`Simulator.start` moves to another port pair when the answer is not its own simulator's). Tests:
  `tests/test_simulator.py`.

## Stock boot writer model

`sim/stock_boot.ipp` models `88a06a7e` and the role-dispatched wrappers: A accesses local media;
B waits on synchronous RPC. The writer returns size on success, 0 on mismatch/invalid input;
an initial select failure returns its raw error. Subsequent storage errors retry without a bound.
Writes precede verification; sector 0 commits last. Failed body verification skips that commit but
still erases the old partition (stock `88a06f9a` → `88a070be`). The stock mismatch flag resets each
sector (`88a06f22`), so an earlier mismatch can be lost, including for aligned-size extra sectors.
B's unchanged installer independently checks the header and every staged byte, then restores partition 0.
Its nonzero-return test can also accept an already matching plaintext image after an initial select error.

`sim/boot_crypto.hpp` uses a deterministic reversible XOR, with a reserved-header `XYFSIM01` marker,
to represent in-place encryption of all padded sectors. Header version 3, plaintext size/CRC, active and
encrypted bytes retain their stock positions. Boot fetch reverses only this marked representation;
older plaintext files remain readable. This models ownership and ciphertext readback, not AES, OTP,
crypt-prepare failure, callback execution, media timing or hardware write atomicity. No private keys are used.
Tests: `test_sim_stock_writer_model` (both roles, retries, verification failures and cuts),
`test_sim_stock_writer` (unchanged B installer, readback/refusal, reboot identity).
