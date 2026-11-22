# XY Forever architecture: safety rules

Part of [XY Forever architecture](../ARCHITECTURE.md).

## Safety rules (all code)

- Every stock function XY Forever calls is CRC-checked against the authenticated
  1.2.1 bootloader (`*_spans.hpp`, `CONSTRUCT_SPANS`, `DIRECT_SPANS`, key UI, peer and
  server spans); the build re-checks each CRC.
- Calls go through noinline wrappers or direct calls; the builder's call audit rejects any
  target it cannot resolve, every forbidden target (SPI flash and metadata, updater, companions,
  stock `.tfw` pipeline, the unbounded stock RPC `889f869e`/`889f7b12`) and any boot-ROM entry
  but memcpy and the OTP read/init (`tfw.check` only). Scoped groups: the five request builders
  only from `file_client`, A's posix stat/open/close only from the marker check, rename only A's.
  OTP is only read, on A or B, through boot-ROM read `0x04000048` and init `0x04000054` as stock does
  (`tfw.check[_role]`, #15); the other ROM entries `0x0400004c` and `0x04000050` (never called by the
  1.2.1 bootloader: unknown, possibly the program entry) are forbidden.
- The audits (call allowlist, CRC spans, symbol rules) are code: they change like other code, and the PR
  names the change.
- Every loop is bounded, and every failure falls back to a bootable state.
- No data loads from L1 instruction memory. No implicit BSS; no memcpy, memset or
  division helpers (`test_xyf_freestanding`).
- The real bootloader is never written. The 1.2.5 bootloader is never installed.

The role-A dispatcher snapshots the pending request pointer once per poll. All
command, storage-RPC and image-transfer checks use that snapshot. An interrupt
arrival after the snapshot waits for the next poll; re-reading the pointer in
each branch let late storage requests bypass RPC dispatch and fail the image
protocol. `tests/native/peer_dispatch_test.cpp` exercises the
production dispatch body with controlled arrivals.

When a stock CRC differs, report its differing word before considering a call or changing a span;
mutable globals are not permission to weaken code checks. Take each processor's role from the stock
role byte, not a cold-time GPIO strap. Every boot path needs its peer side.

## Versioned formats

Every new on-device file and wire format carries a version, and its reader refuses a newer version with a
message, never a crash. For example, an older XY Forever reading a mods list written by a newer one shows
"made by a newer version" instead of crashing at boot.
