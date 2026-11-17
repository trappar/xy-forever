# 001: What is dead code (detail)

Detail of [001-dead-code.md](001-dead-code.md).

## Baseline

- Behaviour: kit **090** (a `recovery/` known-good kit; the maintainer's evidence, not shared) for boot, menu, library, install, reads, writes, dry run and
  reboots, plus the kept post-090 proofs: the guard as on **108** (090's fault-reset guard froze B on v3, Tests 096-103), PEEK, the USB-off notice (097),
  self-test and leave (097/139). No commit equals the target; the cleaned build becomes the known-good kit only after a device pass.
- The device pass must show: PLAY power-on opens home with USB; `status`, `read` (whole and ranged), `screenshot`, `key`, `peek`; `hold on/off`;
  `reboot --to menu` returns home; `continue --dry-run` returns home with its result; `put-file`, `library-add`, `set-default` verified on fresh boots;
  `install` of the cleaned build itself; `guard on` + `guard expiry-test` comes back home without a power cycle (`test-hang` too, if it still does);
  `mtp-test --yes-test` passes every step and a later `read` works without a restart; the MTP row says "not available yet"; the session ends in keepalive, never
  in the OS.

## Order and risk

- Group 1 is candidate-neutral. Groups 2-4 change the product candidate (expected). Groups 2-3 should leave both stage-2 candidates unchanged (reasoned from the
  defines, not built); group 4 changes them through the shared `select_pressed` block in `key_ui.cpp`.
- The guard shares WDOG0, SEC source 3 and `guard_expired` with the watch: remove only the `watch_on` branches and the watch's priority-1 setup; leave
  `guard_arm`, `guard_count_set` and the priority-0 setup byte-for-byte.
- Builder pins must change with the code (`MTP_CALLS <= targets`, write-window and `serve_only` rules, `client_read`, `xy_forever_menu_link`); a missed one
  fails the build closed, never an unsafe build. File write needs `mtp_identity` and the MTP spans: keep.
- Device: markers left by 148-150 may carry bit 0x800; after removal it is ignored. The `mtp_peer` path (B guard reset while A is in `run_mtp`) stays unproven;
  the self-test pauses B's guard, so the device pass will not exercise it.
- Many tests pin source text (`test_xyf_product.py`, `test_xyf_mtp.py`, `test_usb_service.py`, `test_xyf_file_write.py`): edit them in the same commit as the
  code.
