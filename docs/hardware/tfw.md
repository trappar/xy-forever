# Hardware and stock-code facts: firmware packages

Part of [Hardware and stock-code facts](../HARDWARE.md).

## Firmware packages (`.tfw`)

- **Format:** AES-256-CBC, with key and stream in their original byte order.
  - Offsets: header 0x80, key index 0x84, IV 0xf0, ZIP length 0x100, encrypted
    name at 0x380 (256 B, `firmware_secure.zip`), encrypted ZIP at 0x480.
  - CBC continues across the name and the ZIP; padding is PKCS#7.
- **Key indexes:**
  - 0: OTP ROM selectors 3/4 via ROM `0x04000048` / `0x11a05c9c`; verified.
  - 1: selectors 5/6; untested.
  - 0xff: a fixed constant at `0x88a1b97c`.
  - The decrypt routine is `0x88a18cae`. Keys: the private `keys/` folder (never
    print or commit them).
- **Signatures:** the signing (private) key is unknown, so stock rejects any custom
  `.tfw`. The old bootloader also rejects unsigned and legacy containers; key index
  `fe` is no bypass. The vendor public key (signature key index 0) was recovered
  from the three official signatures (one common key, verified by our
  verifier and OpenSSL): `keys/opxy-tfw-public-key.json` in the private folder. Not yet read from
  a device (OTP selector 9; `tfw.check`, #15: `scripts/tfw_probe.py`).
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
- **Companion images:** CYACD has 367 data rows. BLE updates go through a TE
  internal bridge, not the plain Nordic DFU transport.
- **SPI metadata** (record at SPI `0xc000`, member +0xc at `0x8803d890`): `factory:%u`
  is a 16-bit field. The dev-menu gate is software state, not OTP, and no
  retail-to-dev method exists.
- **Stock signature check** (`0x88a17f2a`, read from the listing; confirmed on the
  three packages): signature key index `+0x89` = 0 selects the vendor key, 56 B
  from OTP selector 9 via `11a05b22`, Qx || Qy big-endian (1 = per-device key,
  selector 10, and 0xff = dev constant `0x88a1b99c` need dev mode). SHA-256 (L1
  `11a02102/25aa/2656`) of the 0x300-byte header with `+0x10..+0x50` zeroed; ECDSA
  P-224 (`11a03148`, PKA; curve constants `0x11b00ea4..0x11b00f30`) on its leftmost
  28 bytes; r `+0x14`, s `+0x34`, 28 B big-endian (`11a02b86`). Driver `88a1871c`:
  format, signature, SKU (`88a18288`), payload length `+0x98` and SHA-256 `+0x9c` of
  everything after the header (`88a1844a`). Native: `native/p224`, `tfw_signature`
  (host-tested, not linked); the L1 wrapper's data-load CRC checks cannot be used.
- **Companion update paths:** the stock updater resumes through the SPI marker, which
  XY Forever may not write. The stock backplane handler polls a generic message
  dispatcher with a conditional path to the SPI metadata writer, so skipping the
  stock orchestrator does not make it SPI-safe. Static findings only; these paths remain forbidden.
- **Package contents (official ZIPs, compared offline):** 1.2.1 → 1.2.5 changes the OS,
  backplane (367 rows) and BLE application (244,816 B); all 839 content files
  (125,790,852 B) are identical. 1.1.33 has 829 content files: 825 identical, 3
  changed, 1 added, 11 only in 1.2.1; its firmware label is 1.1.33+1, its OS 1.1.33+0.
  Skip a content write only after comparing the device's actual files.
- **Host tools:** `../tools/tfw-decrypt/`; `xyf_package.py ZIP [--baseline ZIP]
  [--stage DIR]` reviews and stages a decrypted vendor ZIP (host only, unauthenticated,
  bootloader members never staged).

## Companion recovery hazards (offline findings; programming is forbidden)

A silent keyboard/backplane PSoC can prevent NAND boot and the COM recovery path:
keys and power management depend on it. Recovery after interrupted programming without
external hardware is unproven. Do not substitute a stock handler: it can reach SPI metadata.

The Nordic bootloader accepts application data without a signature, platform or rollback
check; the host-only `native/ble_session` models a response-driven transfer, not a safe
on-device feature. Never write its bootloader slots. With an invalid application, `0x18`
inside a DFU session enters System OFF with no proven power-cycle recovery; outside a
session it jumps to the application without validating it. Stock reinstall is therefore
not a proven recovery for interrupted BLE programming. None of these findings relaxes
[SAFETY.md](../SAFETY.md) or authorizes UART probes or programming.
