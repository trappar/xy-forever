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
  last, the other erased, no encryption), the retained cold state, `cold.cpp` (its policy is extracted; an OS
  start stops both processes: `os`; no image: `halted`) and `key_ui` (the menu loop without screen or keys:
  `home_pump`, tick, link check; SCREEN shows a test pattern, KEY does nothing).
- **Resets** (`sim/boot.cpp`): WDOG0 at host calls; a stock reset always lands in the bootloader stand-in. B's probes A
  first: on only if A reset <= `boot_a_window_ms` (400; device 0.15-1.4 s) before B or later, else waits (`b_waits_a`).
- **Not hosted:** the menu's screen and keys, os.ldr itself, CDC ring and USB timing, NAND timing, encryption,
  stage 2; yaffs is a (case-insensitive) host folder.
- **Run:** `python3 -m xy_forever.sim` (`SESSIONS/simulator/` in the private folder: seeded `yaffs2/`, `bootpart.bin`,
  `builds/`; `--port`, `--plain`, `--build N`, `--pre-v1`), then `xyf_host.py --sim 127.0.0.1:7341` (or `XYF_SIM`),
  `scripts/device_pass.py` / `scripts/conformance.py --sim …` (evidence removed when nothing failed, unless `--keep`).
- **Control** (port + 1, one line, JSON reply): `status` (A's `partition`; `boot_writes`, yaffs changes on a boot
  partition; `rw_open`/`unclean_mounts`, read-write mounts never unmounted; `to_a`/`to_b`, link messages relayed),
  `power [play|plain]`, `cut`, `set NAME N` with `a_silent`, `a_slow_ms`, `drop_request`, `drop_body`, `cable_pull`
  (bytes), `space`, `fail_write` (the Nth write, boot partition too), `fail_mount`, `vanish_marker`, `cut_after`,
  `fail_select` (switches, then fails), `fail_unmount` (while set), `fail_open` (A's), `full_path` (240 B, no NUL)
  (storage operations, then a power cut and a PLAY power-on); `swap PATH` (stock path buffer); `inject CLASS OP ARG0
  ARG2 [HEX]` sends A a raw request past B's checks (class 0: generic), `injected TAG` reads A's answer
  (`Simulator.ask_a`); `advance MS` moves both processors' cycle counters ahead, so a device time-out (A's idle
  window, a held window) passes unwaited (`Simulator.advance`, in steps); `reset a|b`, `expire a|b` (if armed),
  `set a_boot_ms|b_boot_ms|a_redeliver|boot_a_window_ms|a_stall_now`. Tests: `tests/test_simulator.py`, `tests/test_guard_reset.py`.
