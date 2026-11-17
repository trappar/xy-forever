# 015: Companion chips: what they are, how stock updates them, what is recoverable (detail)

Detail of [015-companions.md](015-companions.md).

## What the packages carry [M]

Backplane `backplane/bin/backplane.cyacd`, 193,423 B in all three: 1.0.5 in 1.1.33+1 (`19566682a442…`), 1.0.7 in 1.2.1 (`2a7f21819abc…`), 1.0.8 in 1.2.5
(`d2ab1077ba15…`). BLE application `ble/bin/ble.bin`: 1.0.1, 244,280 B (`74549ae976b6…`), the same bytes in 1.1.33 and 1.2.1; 1.0.2, 244,816 B (`5a713c93a5e5…`)
in 1.2.5. Nordic bootloader `nordic_bootloader_{0,1}.bin`, 11,736 B each, 1000.0.10: identical in all three (`96cd41ca6b94…` / `301a8a3acf6d…`).
`blackfin/bin/bootloader.version` (text only): 1000.0.20, 1000.0.20, 1000.0.23.
The CYACD header is `100A11A00000`: 367 rows of 256 B in array 0, rows 25–390, then row 511 last. Rows 0–24 (6.25 KB, the PSoC's own bootloader) are in no
package, so its behaviour cannot be read offline. 1.2.1 → 1.2.5 changes 362 rows.

## How stock 1.2.1 updates them [L]

- **Only from the update orchestrator** `88a2863c` on B, after a signed `.tfw` upload in update mode or on a resume (SPI marker `0xab0d`). Order: content, os,
  bootloader, ble `88a2742c`, ble bootloader `88a2756e`, backplane `88a26f90`, spiboot. It sets the marker first and writes each component's version to SPI
  after success (`11a05312`). A failed handler leaves the marker, so the next boot resumes. **When it programs:** it compares the package version with the
  version recorded in SPI (`0x11b012b8` BLE, `0x11b012c0` BLE bootloader, `0x11b012c8` backplane) as four 16-bit fields (`11a078d2` via `88a267ba`). Equal:
  skip. Any difference, newer or older: program. The chip itself is never asked. A force flag `0x88a291c3` bypasses the gate (package version 0.1.0, one upload
  path).
- **Backplane** `88a26f90`: (1) empty image: skip, then the version gate; (2) enter the PSoC bootloader (`88a26e7e`, 3 tries): TE message 0xee to the
  application, handler to RAW, wait 250 ms, then a raw Cypress Enter-Bootloader carrying a 6-byte key stored at `0x88a290ac` (`88a267e8`; this also works on a
  chip already in its bootloader: "backplane seems to be in bootloader mode"); (3) write `tmp/psoc.cyacd` to yaffs through stock stdio; (4) program with the L1
  Cypress host library `11a09582`, up to 3 attempts: silicon-ID check, then each row programmed and checksummed ("expected device does not match", "flash row is
  not valid"); (5) poll `88a24922` every 10 ms for the application's greeting, send Exit from 1 s on, give up at 2 s. Failure returns 1. `88a26a80` only prints
  the error: HARDWARE.md's "Cypress rows" label for it is wrong.
- **BLE** `88a2742c`: version gate, then the transfer `88a27278`. The transfer needs the chip in DFU (IsInDfu `88a21b22` reads the metadata "mode"), prepares
  it, sends 248-B chunks and compares the Nordic's CRC-16 ("CRC incorrect"). The orchestrator enters DFU (`88a23a28`) and leaves it (`88a2366a`). The BLE
  bootloader handler `88a2756e` sends the slot the Nordic asks for (`88a2374c`).
- **Not a driver layer:** the poll `88a24922` hands PSoC messages to the generic dispatcher `88a21456`, which has a conditional path to the SPI metadata writer
  (TFW-AUTH §3). **1.2.5 bootloader:** the same version-gated handler structure (`boot125-osinstall-safety-001/companion-update-findings.json`); no companion
  change was found; it was not re-read here.

## What the OS versions need

Neither OS contains companion-programming code or a companion-version check; their companion strings are the same apart from source paths [L]. 003 Q5 holds for
the companions: a booted OS cannot program one; it can only reboot into stock update mode when a host asks. 1.2.5 ran on 1.0.7/1.0.1 in Test 053 and in a
community report [M, shallow]. A mismatch costs the companion fixes (1.2.5 notes: BLE MIDI with WIDI and recent macOS; the backplane change is unknown) [I].
Nothing is known to break [U].

## Staged path

0, offline: re-derive the protocols from 1.2.1 (the native code came from the 1.2.5 listing); simulator chips with a cut at every row and chunk. Worst case:
none. 1, read: SPI-recorded versions in RAM (`0x11b012a8..0x11b012d0`) and a handler snapshot. Worst case: B hangs; guard or power cycle; recoverable. 2,
normal-mode metadata over raw UART, as CP001. Worst case: as stage 1. 3, mode change, no write (user's yes): BLE DFU enter, metadata, exit; PSoC Enter, flash
size, verify checksum, Exit, with the application intact. Worst case: a chip left in its bootloader with a valid application; stock sends Exit every boot [L];
recoverable [I]. 4, first write (user's yes): BLE application 1.0.1 → 1.0.2. Worst case: BLE off until XY Forever retries; recoverable [I]. 5, backplane write:
only once O1 is yes and (1) is settled. Worst case today: an external probe.

## Earlier preparation

- **`native/companions`, `ble_session`, `xy_forever/companions.py` (40 tests):** host-only codecs (Cypress packets and row session; BLE header, chunks, CRC and
  response-driven session). No transport, no recovery. Derived from the 1.2.5 listing.
- **Earlier companion prep (the maintainer's archive, not shared):** one commit on build 116, never merged. It holds CP001: an on-demand normal-mode metadata
  probe (`target/companion_probe.cpp`: the 4 raw UART calls, a COBS/CRC codec checked against the stock table, guard required, 300 ms limit), a host runner, an
  audit, with the whole suite (459 tests) passing then. Never run on a device. Still valid: the addresses, the codec and its tests, the audit excerpts. Stale:
  the build wiring, the protocol opcode, the 090/116 kit. **Port the codec, tests and audit into stages 1–2; do not merge the commit.**

## Split

Research (017): protocols re-derived from 1.2.1; Nordic DFU without an application answered or marked [U]; question (1) put to TE or hardware documents. Device,
developer: O1 answered. #31 build: read-only `companions` command (stages 1–2, CP001 port, simulator, conformance, the builder rule above). #32 review: call
closure, ISRs and UART handover of #31. #32 device safe: live versions, modes and hardware revision reported; nothing changed. #33 build: BLE updater with
journal; simulator fault matrix passes. #33 review of the updater. #33 device risky: stage 3, then stage 4, each with the developer's explicit yes; done when
BLE 1.0.2 is reported live and BLE MIDI works. Gate: backplane go or no-go, recorded from the research and the O1 answer.

**Open questions:** (1) BLE first, with the backplane deferred, possibly for good? (2) Which recovery is acceptable: reinstalling 1.1.33 and then XY Forever?
Never 1.2.5? (3) is opening the case with a PSoC programmer ever acceptable? If not, the backplane is never written. (4) who does O1 at the device? (5) allow
the 4 raw UART calls for the read-only inventory?
