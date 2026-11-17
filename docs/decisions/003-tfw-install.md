# 003: Installing the OS part of an official `.tfw`

Research record, 2026-11-14; no device. **[L]** read from the 1.2.1 bootloader listing or our code, **[M]** measured on a file in the private folder, **[I]** inferred. Evidence: the maintainer's (not shared); 125-001 and 125-002 below are its OS studies. Keys: only in the private folder (host tests); the device reads its own from OTP.

## Question

How does XY Forever turn an official `.tfw` on `/yaffs2` into a checked library entry (`firmware/<id>/os.ldr` + `info.txt`) without ever installing the bootloader or touching the companions, and with no keys shipped?

## Answer

**Use our own native decoder (`native/tfw`, `zip`, `inflate`, `ldr`, `hash`, `aes`), with the AES key and the vendor public key read from the device's OTP through two ROM entries. Call no stock decrypt, verify or updater code.** Crypto runs on B (where the stock updater reads the key); A hashes the whole file locally; nothing is staged whole.
1. **Format [L, M].** Outer `babecafe` + TE preamble `beefcafe` at 0x40 (CRC-16, product); crypto header 0x300 B at 0x80: key index +4, IV +0x70, ZIP length +0x80, `TESG` +0x84, format 1 +0x88, signature key index +0x89, SKU +0x8c (0x00084001), version +0x90, ciphertext length +0x98, ciphertext SHA-256 +0x9c, P-224 signature r/s +0x14/+0x34 over the header with +0x10..0x50 zeroed. AES-256-CBC from 0x380: 256-B encrypted name (`firmware_secure.zip`), then the ZIP, PKCS#7, CBC continuous. All three official files: crypto offset 0x80, key index 0, signature index 0, ~97.8 MB [M]. Parts are ZIP members by fixed path; `firmware/blackfin/bin/os.ldr` is 1.27 MB deflated / 2.30-2.37 MB, the central directory ~80 KB at the end [M].
2. **Stock install (B, update mode) [L].** USB mass-storage FatFs RAM disk `88a14820` over SDRAM extents `0x80004000+0x7f00000`, `0x8a000000+0x2000000`, `0x8c03d000+0x3f00000` (descriptor `0x88a149c0` [M]) or MIDI DFU `88a22914`; poll `88a193a0` -> `handle_tfw 88a25d28` -> `88a196cc`: TE header `88a189a4`, decrypt `88a18cae` (verify `88a1871c` = format `88a17d2c`, signature `88a17f2a` with OTP public key (selector 9 via `11a05b22`), SKU `88a18288`, payload hash `88a1844a`; AES key `11a05c9c` from OTP selectors 3/4; engine `11a02288`/`11a023a0`) writes `firmware*.zip` to the RAM disk -> `88a25c6c`: extract all to `/yaffs2/upgrade/` (`88a27d00`), downgrade check, `88a2901a` -> load `88a28cfc` -> orchestrator `88a2863c` (content, OS via `88a06a7e`, **bootloader `88a2775e`**, BLE, BLE bootloader, backplane, spiboot), version-gated, `progress.log`, SPI metadata `0xab0d` + versions (`11a05312`), reset `11a03518`, resume by marker. Leaves: boot partition, maybe SPI bootloader and companions, SPI versions, the `/yaffs2/upgrade/` tree (seen on the device, Test 053 check).
3. **Extraction (recommended).** A: SHA-256 of the ciphertext straight from yaffs as a polled engine job (~98 MB at ~4.9 MB/s, ~20 s [I]); no key needed. B: reads the 0x300 header, checks it (native `tfw::Archive` rules), verifies the P-224 signature natively with the public key from OTP, takes the AES key from OTP, then fetches and decrypts only the central directory and the `os.ldr`, `os.version`, `firmware.version` members (~1.4 MB) through ranged engine reads, inflates `os.ldr` (CRC-32), runs `ldr::inspect` with the boot RAM policy. Memory on B: < 4 MiB (2.4 MB image, entry table, 37 KB inflate) in staging `0x8b800000..0x8bfff000`, below the reboot marker [I]. Total ~30-60 s with a progress screen [I]. A full `package::admit` (every member, ~125 MB inflated) stays for content (decision 006) and companions (M7): same reader, more members.

| Stock entry (all calls the design makes beyond the M2 engine) | What it is | Reaches |
|---|---|---|
| ROM `0x04000054(0)` | OTP init; call only if L1-data byte `0x11b01650` is 0, then set it (mirrors `11a05a32`) [L] | ROM only |
| ROM `0x04000048(sel, buf512)` | OTP read: sel 3/4 AES key index 0, sel 9 public key (0x38 B) [L]; sel 3..6 proven on the device by the OS-context key dump [M] | ROM only |

## Safety

- **Never reached:** SPI-NOR `11a062c6/11a06376/11a06470`; SPI metadata `11a05312` (and parse `11a05340`, gate setter `88a34ed0`, generic dispatcher `88a21456`); updater `88a2863c/88a2775e/88a2830e`; companions `88a2742c/88a2756e/88a26f90/88a26a80/88a27278`; OTP programming (any ROM entry but the two above). Guarantee: the builder's call allowlist admits only the engine calls + the two ROM entries, and `FORBIDDEN_TARGETS` already rejects the first three groups. Propose adding `88a25d28 88a25c6c 88a2901a 88a28cfc 88a27d00 88a196cc 88a18cae 88a1871c 88a14820 11a05340 88a21456 88a34ed0` and a rule that ROM targets are only `040201c4/04000048/04000054`.
- **Bootloader part:** `package_policy::classify` marks any name containing "bootloader" as excluded [L]; the OS-only path never decrypts it; the writer accepts only `firmware/<new id>/{info.txt,os.ldr}`; no boot-partition op (`0x15/0x16`) is used.
- **Keys:** key and AES schedule live only in B's RAM during the job, are scrubbed (`aes.erase`, `read_update_key`), and are outside every census/report/USB-read range. The build embeds no key.
- **Checked before admission:** header magic/format/SKU/lengths; signature (key index 0 only); ciphertext SHA-256 = header; padding and decrypted name `*.zip` (right key); every ZIP name passes `classify` (unknown or unsafe name = reject); one `os.ldr`; CRC-32 + size; `ldr::inspect`; versions parse; after the write, A reads back size + CRC-32.
- **Power cut:** upload: a partial `.tfw` fails length/hash. Verify: read-only. Then write `info.txt` first (bytes + crc32 from the ZIP directory), then `os.ldr`, into a folder that must not exist. A cut leaves either no image (entry unusable) or an image that fails the `info.txt` size/CRC check at CHECK ("size/CRC differ"), because `usable = image_present` (`target/xyf_library.cpp:76`) and `info.txt` is optional [L]. `default`/`once`, boot partition, SPI, companions, `bootloader.dev` are never touched.

Detail: Q5, Proposed split in [003-tfw-install.detail.md](003-tfw-install.detail.md)

## Unproven

Which processor's OTP holds the key (B inferred from the stock updater; A unknown); ROM OTP init/read at cold time; public key at selector 9 never read, its byte order for a native P-224; native `tfw::Archive` never run on a real `.tfw` (synthetic tests only; native `admit` passed on all three decrypted ZIPs [M]); speeds of software AES/SHA-256/inflate, link reads and multi-MB yaffs writes; free space for a 98 MB upload.

## Rejected

- **Stock file pipeline (`88a196cc`/`88a18cae`/`88a1871c`):** FatFs RAM-disk paths only; the disk's extents cover our module `0x8ab00000`, staging and reporter; writes `firmware.zip` [L], reports into the disk and drives the progress UI [I]. A stock feature: breaks Shape.
- **Stock signature `88a17f2a` on a header copy:** direct reach includes FatFs `f_open` (`889ecaf8`) via cleanup paths, the dev flag `88a1dece`, the report sink `88a13fd8`, L1 PKA code (`11a03148`, `11a02be0`) that cannot be CRC-checked by data loads. Fallback only after a full audit.
- **Stock hardware AES/PKTE hash:** L1 drivers with engine state; not needed for ~1.4 MB of decryption.
- **Host-side decryption / shipped keys:** breaks the no-keys goal. Hand-added entries (`library-add`) stay as they are.
- **Decrypt on A with the key sent over the link:** avoid; if the probe shows A's OTP holds the same key, running everything on A is an allowed simplification.
