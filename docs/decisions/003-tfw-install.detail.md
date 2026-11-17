# 003: Installing the OS part of an official `.tfw` (detail)

Detail of [003-tfw-install.md](003-tfw-install.md).

## Q5: can a library-booted official OS (1.2.5) start a bootloader or companion upgrade by itself?

No, by the evidence; confidence **high for "by itself", medium-high overall** (static only). Both OSes have one read-only SPI-flash driver (status 0x05, read
0x03; no 0x06/erase/program), read the `0xab0d` marker only, contain no companion-programming code, no `.tfw`/`upgrade/` strings, and cannot call bootloader
code (125-002, 125-001) [L]. A `.tfw` on yaffs is inert: stock update mode reads only its RAM disk, resume needs the SPI marker [L]. The one route needs a host:
reboot reasons 0x0a/0x1e/0x28 are written only from host-request handlers `8c177a2c`/`8c1ab0a0`; the 1.2.1 bootloader then waits for a host to upload a signed
`.tfw`, which it would install whole, bootloader included. Unverified: which transports reach those handlers (USB only, or BLE MIDI too), indirect calls,
whether the OS writes the NAND boot partition (names present), what "update ble" does. Test 053 booted 1.2.5 from the library without incident [M].

## Proposed split

| Step | Done when | Proof |
|---|---|---|
| build: OS-only extractor (native) | from a Reader + KeyProvider + Writer: header, hash, signature hook, CD, `classify`, `os.ldr`/versions, `inspect`; the three official `.tfw` give `os.ldr` byte-identical to the private `firmware/decrypted` (private-only test, skipped without keys); every corruption fails before the first write; the bootloader range is never read | host |
| build: native P-224 verify | verifies synthetic key pairs and RFC/NIST vectors; header zeroing as stock; key index != 0 rejected | host |
| build: OTP provider on B + builder rules | ROM-direct provider (guard byte, sel 3/4/9, scrub); allowlist and forbidden additions above; key ranges excluded from reports; `TFW_CHECK` command reports only pass/fail + ms; other modes neutral | host + build |
| #14 build: install job | A hash job (polled, bounded), B decode job, write order above, read-back, serial `install` + menu action, progress screen; simulator: real 1.2.5 `.tfw` -> bootable entry; faults (A silent, cable pulled, cut at each write) | simulator |
| #15 device risky: OTP probe | `TFW_CHECK` on A and B: key decrypts the name block, public key verifies the 1.2.1/1.2.5 headers; nothing written | device |

Each needs the developer's explicit yes (SAFETY): the two ROM OTP entries on the allowlist (new stock calls, read-only); the first cold-time OTP read (#15); the
first multi-MB library write (#16). #16 then installs 1.2.5's `os.ldr` only, as SAFETY already allows.
