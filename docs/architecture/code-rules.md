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
- Every loop is bounded, and every failure falls back to a bootable state.
- No data loads from L1 instruction memory. No implicit BSS; no memcpy, memset or
  division helpers (`test_xyf_freestanding`).
- The real bootloader is never written. The 1.2.5 bootloader is never installed.

The role-A dispatcher snapshots the pending request pointer once per poll. All
command, storage-RPC and image-transfer checks use that snapshot. An interrupt
arrival after the snapshot waits for the next poll; re-reading the pointer in
each branch let late storage requests bypass RPC dispatch and fail the image
protocol (069 install stalls). `tests/test_peer_dispatch.py` exercises the
production dispatch body with controlled arrivals.
