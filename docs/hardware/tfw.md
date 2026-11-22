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
- **OTP and the on-device check:** the boot-ROM table, XY Forever's reader, `tfw.check` and A's ciphertext
  hash: [OTP and the package check](otp.md).
- **Companion images:** CYACD has 367 data rows. BLE updates go through a TE
  internal bridge, not the plain Nordic DFU transport.
- **SPI metadata** (record at SPI `0xc000`, member +0xc at `0x8803d890`): `factory:%u`
  is a 16-bit field. The dev-menu gate is software state, not OTP: the file `/yaffs2/bootloader.dev`
  ([storage-server.md](storage-server.md)). On the 1.2.1 bootloader te-boot creates it over Web MIDI (command
  `0xac` after a 16-byte challenge, Ferret's enabler; the web app's Install page, #178): the retail-to-dev
  method. The 1.2.5 bootloader dropped `0xac`.
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
- **Host tools:** `../tools/tfw-decrypt/`; `uv run xyf-package ZIP [--baseline ZIP]
  [--stage DIR]` reviews and stages a decrypted vendor ZIP (host only, unauthenticated,
  bootloader members never staged).

## Companion recovery hazards (offline findings; programming is not built yet)

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
