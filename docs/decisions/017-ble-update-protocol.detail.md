# 017: Bluetooth update protocol, re-derived from the 1.2.1 listing (detail)

Detail of [017-ble-update-protocol.md](017-ble-update-protocol.md).

## 1. Serial links [L]

- **Frame, both chips:** `COBS(raw) 00`, raw = `[route][seq][type][len][payload len<=249][crc8]`; route = dst | src<<4, B is address 8, Nordic 3, PSoC 4. B
  sends 0x83 (BLE) / 0x84 (PSoC); replies arrive as 0x38 / 0x48. seq is a per-peer sender counter, never echoed or checked. CRC-8 over route..payload, start 0:
  per byte `v ^= b; 8 x (v = v>>1 ^ (v&1 ? 0xb2 : 0)); v ^= 0xea` (stock table `0x11900280`; the Nordic's table at bootloader `0x5bcc` is identical). Coder
  `11a08286`/`11a0857a`; the CP001 codec (record 015) matches it.
- **Bluetooth:** UART port 1, 1 Mbaud, hardware flow control (stock open `11a049aa(0x88a3e014+0x80f8, 1, 1000000, 1, "BLE")` in `88a23b68`; Nordic side TX
  P0.14, RX P0.11, RTS P0.20, CTS P0.15). Handler `0x88a3e014` (vtable `0x88a24220`, open flag `+0x7f61`, greeted flag `+0x80f4`). **Nobody owns it when XY
  Forever runs**: stock opens it only in the stay-in-bootloader loop `88a35d4c` and the updater. Before we open it: `B[h+0x7f61]==0`, UART object closed
  (`B[uart+0xc]==0`), CP001's vtable and CRC identity checks, interrupts on, guard armed. Open registers only the UART DMA interrupt `11a048d8` (CP001 audit).
  Close `11a04c80` only once TX is idle (`B[uart+4]==0`).
- **Backplane:** UART0, 1 Mbaud, already open and owned by the stock PSoC handler `0x88a522c8` (keys). Share it the CP001 way: one write `11a04b42`, raw reads
  `11a04b88` inside the menu loop, no key polling in between.
- **Stock timeouts:** greeting at open: type 5 `[01 03]`, 5 ms, up to 1000 tries; metadata 100 ms; start 1500 ms; header, chunk and finish 1000 ms each. The
  request primitive `11a0715a` polls every 100 µs.
- **Never call:** the poll `88a2411a` (vtable+0x14: unsolicited frames go to the generic dispatcher `88a21456`, MIDI `88a1cb58` and the greeting handler
  `88a23fde`), nor `88a23a28 88a2366a 88a2383e 88a2374c 88a27278 88a2742c 88a2756e 88a2863c 11a05312`.

## 2. Read-only queries

- **Type 9, metadata** (`83 01 09 00 4f` -> wire `04 83 01 09 02 4f 00`; reply type 10, text) [L]. Application:
  `mode:normal;product:OP-XY;sw_version:1.0.1+0;mac:..;serial:..;product_serial:..;name:..;sku:..`. Nordic bootloader:
  `mode:bootloader;product:OP-XY;sw_version:<settings page, 0.0.0+0 if no valid app>;bl_version:1000.0.10+0;boot_slot:N` (N = the slot it would flash, not the
  running one). PSoC: stock asks it for `sw_version` or `app_sw_version` (`88a218de` from `88a35e3e`) [L]; its other keys [U]. Export only version, mode and
  product; drop mac and serials.
- **Hardware revision:** no query reports it. The application greets on its own, type 5 `[0xaa, rev]`, every ~1 s until a type 6 reply (rev = Nordic straps
  P1.02-P1.04) [L]; the bootloader greets `[0xbb]` only, every ~100 ms [L]. B's handler logs payload[1] as "backplane revision" (`0x8803d8a1`) [L]. Read it by
  listening, without replying.
- **Change nothing on any chip [L]:** types 1 (echo), 5 (reply 6 `[40]`) and 9; and `0x0d[0x10]` (reply `[54, mode]`), but only inside a bootloader session.
  Never send `0x0b` (writes serials and calibration to flash), `0x0d` (any sub-op), 6 (stops the application's greeting), `0x31`, `0xaa`, `0xa0-0xa6`.
- **Expected mode:** every Nordic reset ends in its bootloader loop, and it jumps to the application only when B answers its greeting with `0x0d[0x18]` (stock
  `88a23fde` with marker 0; the OS has the same handler) [L]. So at XY Forever time on a cold boot, expect `mode:bootloader` with a valid `sw_version` [I].
  **015:** a "silent" BLE chip at that point is normal, not a fault.
- **SPI record in RAM** (`11a05312` writes it to SPI) [L]: marker `0x11b0128c` (0 = normal, `0xab0d` = update pending; `0xf0f0` is the image default before the
  SPI copy loads, and greetings are ignored while it is set). Then eight-byte version slots, each 4 x u16 LE (major, minor, patch, build; printed `a.b.c+d`):
  `0x11b012a0` package, `a8` OS [I], `b0` bootloader [I], `b8` BLE, `c0` BLE bootloader, `c8` backplane. Reading these changes nothing.

## 4. Nordic bootloader with no valid application [L]

It stays in its serial loop with no timeout, feeds its watchdog, greets `[0xbb]`, answers type 9 and accepts a new session: B can reach it, and XY Forever can
retry. Keys, USB and the PSoC do not depend on it [I, high]. Hazards: a pre-session `0x18` (the OS or stock greeting handler [I]) jumps into the broken image;
whether a fault or watchdog then returns the chip to its loop is **[U]**. An in-session `0x18` means System OFF: whether a device power cycle cuts the Nordic's
power is **[U]**; a schematic or TE would settle it. Whether the OS boots normally beside it is **[U]**: XY Forever should finish or retry an interrupted update
before booting the OS.

## 5. Images [M, L]

`.tfw` ZIP members `firmware/ble/bin/ble.bin` (raw image linked at `0xc000`, no header; its version exists only in the metadata string), `ble.version` (text
`1.0.1+0@5b06dea7…` / `1.0.2+0@dd8d0b65…`), `nordic_bootloader_{0,1}.bin` + `nordic_bootloader.version` (`1000.0.10+0`). 1.0.1: 244,280 B, SHA-256
`74549ae976b6c4c334755addb93d1f73616916eabc5c0ab1e37ad9bf71d39626` (1.1.33 and 1.2.1). 1.0.2: 244,816 B,
`5a713c93a5e5ccd1af4ceb5e21d8cfd2a1f37cf6d9b197da96f70f62234052fe` (1.2.5). The chip checks only the magic, length, CRC-16 and address range: **no signature,
platform or rollback check**. So 1.0.2 is accepted beside the 1.2.1 bootloader, and 1.0.1 can be written back [L, untested]. Any image with a correct CRC is
accepted too: our SHA-256 match against these two is the only guard.
