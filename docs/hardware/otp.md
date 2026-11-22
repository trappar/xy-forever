# Hardware and stock-code facts: OTP and the package check

Part of [Hardware and stock-code facts](../HARDWARE.md). The `.tfw` format and its keys:
[firmware packages](tfw.md).

## OTP and the package check

- **OTP (read from the 1.2.1 listing).** Boot-ROM jump table, 4-byte entries:
  `0x04000008` ROM loader (via `88a3477a`); `0x04000048` read (R0 selector, R1 buffer; R0 the
  answer); `0x0400004c` (only `88a2123c`: selector 0x13, a 16-byte buffer filled before the call
  and read after; a challenge-response, not a key read [I]); `0x04000050` never called;
  `0x04000054` init (R0 = 0). Stock wrappers: init `11a05a32` (once per boot, guard byte
  `0x11b01650`), read `11a05a1e`; public key `11a05b22` (sel 9, answer != 0, 0x38 B; sel 10 for
  index 1), AES key `11a05c9c` (sel 3 to scratch, sel 4 to scratch+0x10, each answer == 1, 0x20 B;
  5/6 for index 1). Scratch: SDRAM `0x8803d8c4` (0x200 B, cleared before, not after).
  Callers: the boot fetch `88a07240` on both processors when the boot image is encrypted
  (`88a06a0c`: AES key to `0x88072de0`), the Track-5 writer `88a06a7e` on B every install, update
  mode on B (`88a18cae`, `88a17f2a`: sel 9). So sel 3/4 already run in XY Forever's context on B;
  sel 9 never has. XY Forever's reader: `target/otp_keys.cpp` (A or B, solely owned by shared `tfw_check_run`).
- **Reader audit and probe:** `xy_forever/otp_audit.py` confines the provider to `tfw_check_run`,
  rejects the old update-key reader, and rejects ROM-table or provider pointers in aligned words of
  `.data` and `.rodata`. `scripts/tfw_probe.py --roles a,b` checks both processors with explicit approval for the first
  OTP read (#15); without `--roles` it keeps the legacy B probe. Versioned `tfw.check_role` (0x48)
  uses the existing tagged path-query transport (separate TFW/version-1 tag). A uses its existing
  read-only session, a 1 KiB ciphertext cache and reopen/skip, with a 300000-byte cut bound; no new
  stock or ROM entry. B waits at most 5 s for each A request. Both execute the complete native
  signature/AES/directory check with their own OTP. A readable negative is recorded and the next
  role runs; it does not imply transport failure. Its per-check reply timeout defaults to 5 s (`--check-timeout`); after a timeout or
  disconnect it reports "power cycle needed" and sends nothing else. It never sets hold or a guard,
  resets, or boots. It logs and removes only its attempted cuts while the device answers; a lost
  connection leaves their exact paths in the session summary. Success starts keepalive. The simulator
  models independent A/B keys, ROM hang/fault (`a_otp_fail` / `otp_fail` = 5/6), init/guard state
  (`a_otp_initialized` / `otp_initialized`), counters, and stale RPC versions (`tfw_rpc_version`).
- **A hashes ciphertext in a read-only job (#14).** The input is an immutable path under
  `/yaffs2/xy-forever/`. The version-1 hash path query returns the declared total file size at start.
  Each pass of A's menu loop reads at most 4 KiB and hashes only the ciphertext. A verifies the
  declared end against actual EOF, closes the file, and then returns the digest to matching-path
  queries. Cancel, engine teardown, or ten seconds without a matching poll release the job.
  The shared B reader holds the same inbox path under exclusive storage ownership. It checks size
  and EOF when reopening and exposes the ZIP only after the signed header and A's hash agree.
