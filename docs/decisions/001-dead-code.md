# 001: What is dead code (plan item 0.2)

Research record, 2026-11-14. Rule: code goes when it only served stock MTP, its watch/recovery (builds 091-150), a finished probe, or a flow no build uses any more (`--xyf-product-v2`, `--xyf-mtp`). It stays when a plan item needs it or the device pass (0.5) can check it.

## Remove (in this order, one commit per group; tests and `--xyf-product` + `--verify` after each)

1. **Host only** (why: they drive device code that groups 2-3 remove; firmware unchanged, `scripts/neutral.py` must match every mode).
   `xyf_host.py`: command `isr-probe`; `mtp-test` options `--isr --auto --fault --watch --watch-test --watch-state --log-only --stall` and `stall_text()`; `usb_proto.ISR_PROBE`. `scripts/memscan.py` (served build 120's USB-string report mode, itself removed in 140-142); `scripts/sessions/run072..run096.sh` (one-off sessions of old builds; 0.5's `device_pass.py` replaces them).
2. **Stock MTP menu mode, MTP watch, fault injection, ISR probes** (why: abandoned, PLAN "Shape"; the ISR probe 095 is finished: a stock SPI call works inside the USB interrupt).
   - `key_ui.cpp/.hpp`: both `mtp_mode()`, `show_mtp`, `mtp_lines`/`mtp_hint`, screen 3, `mtp_active`/`mtp_stop` in `on_key`, `clock_now`, `flush_line`, `mtp_sample`, `mtp_log*`, `mtp_auto_ms`/`mtp_used`/`mtp_fault`, `request_mtp`, `recovery_screen`, the `mtp_stall()` status line, `mtp_again()`. The MTP row then shows the existing "MTP mode: not available yet." (as on 090).
   - `usb_service.cpp/.hpp`: watch state, `watch_note`/`watch_recover`/`watch_tick`; the `watch_on` branches in `guard_expired`, `guard_kick`, `home_pump`; `mtp_watch_on/off/loop`, `mtp_test_restart`, `mtp_stall`, `mtp_to_cdc`, `hand_usb_to_stock`, `home_restart`, `mtp_restart`, `mtp_next`/`mtp_now`, `marker_mtp` (also in `flags()`, `home_begin`), `a_mtp_guard`; `mtp_test` modes 6 and 9 and the 8-byte ISR modes (`mtp_isr`, `mtp_callback`, `isr_test`, `isr_phase`); `mtp_do` steps 71-73 (no caller); `isr_probe`, `probe_callback`, `Probe`, `t_isr_probe` (type 0x06 retired); constants `stall_*`, `watch_*`, `mtp_watch_count`, `mtp_beat_ms`, `mtp_stall_ms`.
   - `storage_server.cpp/.hpp`: `mtp_fault`, `mtp_fault_item`, `mtp_faults`, `mtp_beat_item`/`mtp_beat_alive`, `mtp_beat_limit_ms`, `mtp_idle_limit_ms` and the `watched`/`beat_ms`/`busy_ms` logic in `run_mtp`; `mtp_start`, `mtp_finish`, `mtp_usb_busy`. `xyf_boot::mtp_command` (`file_boot.cpp`, `xyf_boot.hpp`).
   - `xyf_build.py`: drop `88a1d69e` (stock MTP start) and `88a34fec` (stays in `USB_CALLS`) from `MTP_CALLS`. Tests: `test_xyf_mtp` `test_recovery_limits` and the fault/beat pins of `test_serve_loop_pins`; `test_xyf_product` `MtpFromHomeTest`, `IsrProbeTest`, the ISR parts of `MtpSelfTestTest`.
3. **`--xyf-mtp`** (Test 033 offline build, never on the device, MTP ends by power-off): its `MODES`/CLI/`GC_MODES` entries and `features()` case; the non-v3 `run_mtp` branch; `peer_services` `serve_only` and the builder's `-DXYF_MTP` for `peer_services`; `menu_link::mtp_command`, `storage_server::mtp_requested`; verify rule `has('serve_only') == mtp` becomes "never"; `test_xyf_mtp.test_builder_mode`.
4. **`--xyf-product-v2`** (064/068 two-session flow, superseded by v3 since 069/075; kit 090 is v3): mode, flag, `FILE_VARIANT_REPORTS` entry; branches compiled only with `XYF_USB_SERVICE`/`XYF_MENU_LINK` and without `XYF_ONE_SESSION` (~240 lines by `unifdef` in usb_service/storage_server/menu_link); code unreachable on v3: `usb_service::run`, `menu_pump`, `enter_service`, `menu_reboot`, `menu_boot_requested`, `host_present`, the body of `xy_forever_menu_link()` (returns at once on v3) with its `step`/`settle`/`command`/`read_storage`, `storage_server::client_read`, key_ui's `KEY_UI_MENU_LINK` and `selection`/`select_pressed` blocks, and the builder pins on them (`xy_forever_menu_link`, `storage_server11client_read`). Do not blanket-remove `#else` branches of `XYF_ONE_SESSION` in `key_ui.cpp`/`file_boot.cpp`: the stage-2 modes compile them.

Size (estimate from `unifdef` and reading, not measured after removal): about 2,000 lines (~650 MTP/watch firmware, ~600 v2, ~170 host tool, ~330 scripts, ~300 tests); 9 files deleted.

## Keep (looks dead, is not)

- **A's MTP executor = the file engine's A side (M2: 2.1, 2.2; rename there, not in 0.4):** `decide_mtp`, `valid_mtp_path`, `mtp_protected_path`, `mtp_boot_layer`, `serve_mtp` incl. the `dirent_zero` refusals (128/131), `open_mtp`/`close_mtp` (the read-write window), `run_mtp` serving until `mtp_leave_item`, `mtp_peer` (B restarted: close storage, restart A; unproven, 150 froze there). Proven by the 097 self-test and real MTP sessions on 130/138/142.
- **Leave without a restart (139):** A's `mtp_leave_item` branch, then the library rescan and catalog re-encode in `file_boot.cpp`; B's `mtp_leave`, `xyf_boot::refresh_catalog`, `key_ui::refresh_catalog` (also used by set-default).
- **B's main-loop self-test** `mtp-test --hold-ms` (097: `mtp_enter`, `mtp_selftest`, `mtp_do` 1-10, `mtp_leave`, `t_mtp_test` length 4) with `MTP_TEST_CALLS`: the engine's first light (B's stock stat/statfs/opendir/readdir/open/read/close from our loop) and the only path that still reaches the executor, so 0.5 can test it. `XYF_MTP` stays its switch (product only); `MTP_CALLS` keeps `889f72e4 889f6b7c 889f6dca 889f44f4`.
- **`target/mtp_spans.hpp`, `mtp_spans()`, `check_mtp_spans`, `mtp_identity`:** also the CRC gate of USB file write. Do not shrink or regenerate in 0.4 (do it with 0.3/2.1 via `re/mtp_reach.py`, `re/spans.py`).
- **Guard of the plain USB service** (proven end to end on 108): `guard_arm/kick/count_set/disarm/pause/resume`, `guard_expired` with USB stop before reset (107) and the stuck-PC screen (112), A's `a_guard`, `guard expiry-test` (`guard_muted`, `guard_test_count`), `test-hang` (not re-run since 107). Only the watch hooks go.
- **Read-only diagnostics** for 0.3 and M3 (own USB driver): PEEK (093/096 limits), `scripts/heap.py`, `spandiff.py`, `irqshow.py`, `usb_alive.py`; `scripts/mtp_probe.py` is a libusb MTP client for 3.4/3.6.
- **Stage 2** (`--xyf-stage2`, `--xyf-stage2-menu`, `xyf_stage2*`, `stage2_*.cpp`, `xy_forever/xyf_stage2.py`): the starter/main split of GOALS, plan 4.7. They still use the narrow layout, `XYF_SMALL_CRC` and the embedded OS, so `NO_EMBEDDED_MODES` and the embedded path in `cold.cpp`/`outer.py` stay.
- **Menu MTP row** (`Action::mtp_mode`, stage-2 `request_mtp`): entry point for our own MTP (3.6). `XYF_STATE_WRITE`: see open question 2.

## Baseline

- Behaviour: kit **090** (a `recovery/` known-good kit; the maintainer's evidence, not shared) for boot, menu, library, install, reads, writes, dry run and reboots, plus the kept post-090 proofs: the guard as on **108** (090's fault-reset guard froze B on v3, Tests 096-103), PEEK, the USB-off notice (097), self-test and leave (097/139). No commit equals the target; the cleaned build becomes the known-good kit only after 0.6.
- The device pass must show: PLAY power-on opens home with USB; `status`, `read` (whole and ranged), `screenshot`, `key`, `peek`; `hold on/off`; `reboot --to menu` returns home; `continue --dry-run` returns home with its result; `put-file`, `library-add`, `set-default` verified on fresh boots; `install` of the cleaned build itself; `guard on` + `guard expiry-test` comes back home without a power cycle (`test-hang` too, if it still does); `mtp-test --yes-test` passes every step and a later `read` works without a restart; the MTP row says "not available yet"; the session ends in keepalive, never in the OS.

## Order and risk

- Group 1 is candidate-neutral. Groups 2-4 change the product candidate (expected). Groups 2-3 should leave both stage-2 candidates unchanged (reasoned from the defines, not built); group 4 changes them through the shared `select_pressed` block in `key_ui.cpp`.
- The guard shares WDOG0, SEC source 3 and `guard_expired` with the watch: remove only the `watch_on` branches and the watch's priority-1 setup; leave `guard_arm`, `guard_count_set` and the priority-0 setup byte-for-byte.
- Builder pins must change with the code (`MTP_CALLS <= targets`, write-window and `serve_only` rules, `client_read`, `xy_forever_menu_link`); a missed one fails the build closed, never an unsafe build. File write needs `mtp_identity` and the MTP spans: keep.
- Device: markers left by 148-150 may carry bit 0x800; after removal it is ignored. The `mtp_peer` path (B guard reset while A is in `run_mtp`) stays unproven; the self-test pauses B's guard, so 0.6 will not exercise it.
- Many tests pin source text (`test_xyf_product.py`, `test_xyf_mtp.py`, `test_usb_service.py`, `test_xyf_file_write.py`): edit them in the same commit as the code.

## Open questions (lead or user)

1. Stage 2: keep the two modes as they are until 4.7's research (recommended), or drop them now and rebuild the split on the product layout at 4.7?
2. `XYF_STATE_WRITE` (menu set-default, compiled by no mode, proven in Test 030): keep parked or delete?
3. Rename `mtp-test`/`XYF_MTP`/`*_mtp` in M2 (recommended, keeps 0.4 a pure removal) or now? Fold the product switches into one `XYF_PRODUCT` after group 4 (they become equivalent)?
