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
- **Device time** (`sim/clock.cpp`, every run: tests, `uv run -m xy_forever.sim`, the web app, `conformance.py
  --sim`): a clock only the simulator moves, never host time. The board steps it (1 ms after activity, doubling
  up to 10 ms, 100 ms with B at rest, never past a known event: a reset delay, a delayed link message, a fault's delay, a held key's
  repeat) only while both processors are idle (no link message in flight, each main thread in a wait or polling the clock with nothing
  done) and something is due: an `advance`, a board event, B waiting on device time away from its menu loop's
  pause (a delay, a time-out) or on A for a host request it passed on, or work since the last step (with B at rest: only while B owes a host request its
  answer, or for 1 s past the last power-on, USB data or control command, so a device that polls on its own comes
  to rest). Otherwise it stands still and every process blocks with no CPU: an idle device's own timers
  (keepalives, a deferred save, an idle window's close, a frame's idle reset) wait for an `advance`, which is how
  a host's pause reaches the device (host tools pause with `transport.sim_pause`, tests with `Simulator.advance`,
  `until`, `settle`: until nothing is done). A relayed message and a busy processor's clock reads add their
  own time, so no device time-out depends on host load (sim/clock.cpp has the rule). Interactive runs pass
  `--idle-wall` (`mise run sim`): where the clock would stand still it follows the wall instead, so a person sees
  the device's own timers fire. Tests never pass it: their device time moves only by `advance`, `until`, `settle`.
- **Resets** (`sim/boot.cpp`): a stock reset always lands in the bootloader stand-in (no WDOG0 model). B's probes A
  first: on only if A reset <= `boot_a_window_ms` (400; device 0.15-1.4 s) before B or later, else waits (`b_waits_a`).
- **Not hosted:** the menu's screen and keys, os.ldr itself, CDC ring and USB timing, NAND timing, real encryption,
  stage 2; yaffs is a (case-insensitive) host folder.
- **Run:** `mise run sim` (`uv run -m xy_forever.sim --idle-wall`; `SESSIONS/simulator/` in the private folder: seeded `yaffs2/`, `bootpart.bin`,
  the stock image; `uv sync` builds it; `--port`, `--control`, `--plain`, `--version V`, `--pre-v1`, `--ask`), then `uv run xyf-host --sim 127.0.0.1:7341,7342` (endpoint, control port; or `XYF_SIM`),
  `scripts/device_pass.py` / `scripts/conformance.py --sim …` (evidence removed when nothing failed, unless `--keep`).
- **Ports:** never a picked number in tests (`--port 0`): the launcher binds the control port to port 0 and hands
  it over; B binds its endpoint to port 0 when USB first comes on and keeps that port (status `port`; its "USB on:
  listening on" line). B listens only while USB is on.
- **Control** (its own port, `--control` or the held one; one line, JSON reply): power, cuts, faults, device time, keys, status:
  [simulator control](simulator-control.md).

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
Tests: `tests/native/sim_stock_writer_test.cpp` (both roles, retries, verification failures and cuts),
`test_sim_stock_writer` (B installer; its fault cases: the C++ test).

