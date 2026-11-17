# 017: Bluetooth update protocol, re-derived from the 1.2.1 listing

Research record, 2026-11-14; nothing built, no device. **[L]** read from the 1.2.1 bootloader listing (the private `analysis/bootloader-121-listing`) or the Nordic images (Thumb disassembly, bootloader 1000.0.10, application 1.0.1/1.0.2), **[M]** measured on files, **[I]** inferred, **[U]** unknown. Scratch and regeneration scripts: the maintainer's (not shared). Corrects 015 in four places (marked **015:**).

Detail: §1, §2, §4, §5 in [017-ble-update-protocol.detail.md](017-ble-update-protocol.detail.md)

## 3. Bluetooth application update

**Stock 1.2.1 [L].** The orchestrator `88a2863c` sets marker `0xab0d`. Enter DFU `88a23a28`: send `0x0d[0xee]`, no wait (the application replies `0e[54]`, then reboots into its bootloader); wait 250 ms; up to 20 type-9 polls until `mode:bootloader`; then up to 100 x 5 ms for the bootloader's `[0xbb]` greeting, which the greeting handler answers with `0x0d[0xee]` (marker `0xab0d`) to start the session. Enter failure aborts the whole update. **015:** next, `88a2383e` reads the *live* `sw_version`/`bl_version` in DFU into `0x11b012b8`/`c0`, so the version gates compare the package with the chip, not with SPI. Handler `88a2742c`: size <= 0x70000; gate (equal = skip); transfer `88a27278`: package CRC-32 check (`88a266e4`); IsInDfu (3 tries); start `88a23ce4`; header and chunks `88a27136`; finish `88a23efe`; then copy the version to `0x11b012b8`. At the end: marker 0, SPI write, then exit `88a2366a`: send `0x0d[0x18]` in the session; the chip resets, greets `[0xbb]`, and the handler (marker 0) answers `0x0d[0x18]`: application. Waits up to 2500 x 1 ms, then 20 polls for `mode:normal`. A failed handler leaves the marker, and the update resumes at the next boot (at most 2 resumes).

- **Messages, all type `0x0d` to the chip; every reply is type `0x0e` with `54` = OK, `84` = error (text may follow) [L].** Before a session, the bootloader acts only on `0x18` (jump to `0xc000` with **no validity check**) and `0xee` (start the session; no reply); any other sub-op is dropped silently.
- **Start** `[02][ver 4 x u16 BE][kind b0][platform u32 BE]`, 14 bytes; example for 1.0.2: `02 0001 0000 0002 0000 b0 00000000`. The chip **erases the application settings page** (application invalid from here), resets the address to `0xc000` and the CRC to 0. It does not check the version or platform.
- **Header** `[03]` + 64 bytes, sent alone, BE: 0-3 `beefcafe`, 4 `03`, 5-8 size, **9-10 CRC-16**, **11-18 version**, 19-22 `0001f000`, 23-26 platform (stock: the word at `0x8803d8a4`), 27-63 zero. Header `88a2635c`; the same order in 1.2.5's parser. The chip parses only the magic, size and CRC.
- **Chunks** `[03][<=248 data]` in order; each 4 KB page is erased and written as the stream passes it, so some replies come ~100 ms later [I]. CRC-16/XMODEM (poly 0x1021, start 0; stock table `0x11900000`): `0x279b` for 1.0.1 (985 chunks), `0x3b4c` for 1.0.2 (988) [M]. No chunk is ever retried: one bad reply ends the transfer.
- **Finish** `[05]`: the chip checks bytes received = size + 64, all writes in range, and the CRC. It then writes the magic `0x5a82b619` and the version to the settings page (`0x1000`) and replies `54`. It never sends `92` (stock's "pending" path is dead with this bootloader). It stays in the session.
- **Exit:** `0x18` in the session after a valid finish resets the chip into its loop; answer its `[0xbb]` greeting with `0x18`, then confirm `mode:normal` and the new `sw_version` with type 9. **With an invalid application, `0x18` in the session sends the chip into System OFF with no wake source.** To abort, send `0x0d[0xee]` again (reboots into the bootloader; the session is lost).
- **Slots:** the Nordic bootloader is A/B (slot 0 at `0x3000`, slot 1 at `0x8000`; records at `0x2000`/`0x7000`; stage-0 at `0x0`, in no package). It only ever flashes the other slot (kind `b1`, `88a2756e` via `boot_slot`). **We never send `b1`.** The application has one slot, `0xc000-0x7c000`.
- **Power cut:** before start: no change. From start to finish: no valid application; at the next boot the chip is in its loop, reachable, reporting `sw_version:0.0.0+0`. A cut during the settings write leaves a tiny window with the magic but no valid version [I]. After finish: valid.
- **1.2.5 bootloader:** the same functions, constants and order (op-sequence diff 0.81-0.90, register allocation only); the platform word moves to `0x8803d8a8` [L].
- **Native `ble_session`/`companions`/`companions.py` differed (fixed, host only):** (1) **the header puts the version at 9-16 and the CRC at 17-18, so every update would fail its final CRC and leave the application invalid**; (2) one `0xee` only: from the application, the second `0xee` (the answer to `[0xbb]`) is missing, and start gets no reply; (3) the `0e[54]` from the application is not consumed; (4) there is no exit, `0x18` handling or live check, and `complete` arrives before the application runs; (5) the platform word is no image guard (the chip ignores it); (6) the `92` pending phases never happen. Matching: start, chunk prefix and size, CRC-16, the 0x70000 limit, timeouts.

## 6. Recovery (no external hardware)

| Step | Worst case | Recovery |
|---|---|---|
| (a) open UART1, type 9, listen; PSoC type 9 | B hangs in a stock UART call | guard or power cycle; no chip changed [L] |
| (b) `0xee` to the application | chip left in its loop | next boot: OS sends `0x18` [I] |
| start .. finish | no valid application, BLE off | XY Forever retries from the journal [L reachable; not proven on device] |
| wrong `0x18` in the session | System OFF | **not proven recoverable [U]**: forbidden in code unless finish returned `54` |
| exit / live check | application valid, not started | answer the greeting with `0x18`, or next boot [L] |

Official 1.2.1 install as a fallback: it reprograms BLE whenever the live version differs from 1.0.1 (**015**), but it aborts if Nordic DFU entry fails, and with no valid application its greeting handler sends `0x18` into the broken image first: **not a proven recovery [U]**.

## 7. Replacement text (for a later build item)

- SAFETY recovery step 3: "It reprograms the Bluetooth application whenever the chip's live version differs from 1.0.1 (it asks the chip), and the backplane only when its recorded SPI version differs from 1.0.7; it never changes the Nordic bootloader (same version)."
- GOALS, Companion chips: "**Companion chips.** Only the Bluetooth application, by XY Forever's own updater over the serial link (record 017), from an official image whose SHA-256 matches; the backplane chip is never written."
