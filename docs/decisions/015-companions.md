# 015: Companion chips: what they are, how stock updates them, what is recoverable

Research record, 2026-11-14; no device, nothing built or written. **[L]** read from the 1.2.1 bootloader listing (the private `analysis/bootloader-121-listing`)
or OS strings, **[M]** measured on files in the private folder, **[I]** inferred, **[U]** unknown, **[D]** design choice. Prior evidence (re-checked where
cited): the maintainer's (not shared).

## Answer

The Bluetooth application can be updated in a way that is recoverable without external hardware, by our own updater over raw UART calls (option b). **The
backplane cannot be shown to be recoverable with the evidence we have: a failed backplane update may need a debug probe.** Order: the read-only inventory first,
then BLE. The backplane stays unsupported until unknowns (1) and (2) below are settled.

## The chips

- **Backplane/keyboard: Cypress PSoC**, silicon ID `0x100A11A0` rev 0 [M]. B talks to it over UART0 at 1 Mbaud (`88a24864` opens "PSOC" with `11a049aa`), with
  TE framing (COBS + CRC-8) and handler object `0x88a522c8` [L]. It owns the keys (request 0x41, reply at `0x8807ce7e`), battery and charge (0x36/0x37), power
  modes (0x30/0x31: active, standby, off, charging; it wakes the Blackfin to charge), the USB current limit (0x33/0x34) and the USB-C role
  [L strings, usb-vbus-001].
- **Bluetooth: Nordic nRF** (Cortex-M application, Zephyr strings) with TE's DFU bootloader in two slot images [M]. On B, UART `0x20087000`, handler
  `0x88a3e014`. It is opened only on the stay-in-bootloader path (`88a35d4c`) and by the updater, not on a normal boot [L]. It does BLE MIDI [I]. **A silent BLE
  chip** does not affect boot, keys or XY Forever [I, high].
- **A silent PSoC [L]:** the link open `88a24864` greets it; if that fails, it logs "psoc might be stuck in bootloader, try to exit", sends Cypress Exit
  (`88a26f5e`), waits 1.5 s and goes on. The key check `88a20852` (one 0x41 request, 100 ms limit) logs "no keys from psoc" and returns 1. Main (`88a357a0`)
  treats that as held ("bootloader held, stay in bootloader!") and never loads the NAND `os.ldr`. **So while the PSoC is silent, XY Forever, the OS and hold-COM
  all fail** (COM is read the same way). The stock menu ("1. upload firmware … 3. dev menu") is driven by keys [L strings, I]. `wait_for_battery` `88a1dbae`
  also polls the PSoC; with no reply it probably leaves after ~3 s through the local current-limit fallback `88a1c91a` [I]. The stock boot logs in 25 of our
  device reports (tests 017–044, `075/`) show the PSoC always greeting at once ("[key status] bootloader: 0", no "stuck" line) [M].

Detail: What the packages carry, How stock updates, OS versions, Staged path, Earlier preparation, Split in [015-companions.detail.md](015-companions.detail.md)

## Recoverability

- **BLE application [I, medium-high]:** a cut or rejected transfer leaves the Nordic bootloader in DFU. We never write that bootloader. The device still boots,
  the keys work and XY Forever can retry. A stock reinstall would not repair it: SPI still records 1.0.1, so the gate skips BLE. Unknown [U]: does the Nordic
  bootloader stay in DFU by itself when the application is invalid (stock always enters DFU through the application, 0xee)? Is a downgrade accepted? Wrong-image
  guard: the platform word in TE's header (product 0x21 + hardware revision; native notes).
- **Backplane: may need external hardware.** A cut mid-program most likely leaves the PSoC in its own bootloader: rows 0–24 are never written and row 511 goes
  last [I]. Then there is no key reply, and XY Forever, hold-COM and the OS are all out of reach [L]. A software recovery needs all three of: (1) **[U]** the
  Blackfin powered at a cold start while the PSoC sits in its bootloader (the PSoC runs the power modes, and its bootloader code is not available); (2) **[U]**
  the stock update disk reachable with no key press on this dev-marked device; (3) an official package whose backplane version differs from the SPI record and
  whose bootloader is 1000.0.20: that is 1.1.33, backplane 1.0.5 [M versions; I that it installs]. 1.1.33 also replaces XY Forever with OS 1.1.33; XY Forever
  can be reinstalled from the dev menu once the keys work again. The 1.2.5 package would also install its own bootloader: forbidden, and permanent. If any of
  the three fails, only an SWD probe on the PSoC helps. A wrong image is refused by the silicon-ID check [L strings].
- **What a device check without a write can settle:** observation **O1**: power on holding COM with USB connected to a Mac, and see whether a disk appears
  without any further key press. That answers (2). No such check settles (1): that needs TE, a schematic or a look at the board.
- **Unproven, and corrections:** Unknown [U], besides the items marked above: whether the PSoC answers normal-mode metadata (CP001 assumed it does), and the
  device's hardware revision. SAFETY recovery step 3 says an official 1.2.1 install "may rewrite the companions": that holds only when the SPI record differs
  from the package. GOALS says companions "use the stock update routines": that conflicts with rejecting option (a).

## Options

- **(a) Stock routines: rejected.** `88a26f90`/`88a2742c` are stock features: they use the SPI gate, yaffs stdio, the dispatcher with its SPI-writer path, and
  leave DFU entry to the orchestrator. No subset of them is a driver layer.
- **(b) Our own updater over raw UART: recommended.** It uses CP001's 4 calls (open `11a049aa`, write `11a04b42`, read `11a04b88`, close `11a04c80`), the native
  `companions`/`ble_session`, and our own bounded loops on B. The image is staged and hashed in B's RAM before any traffic, so B never waits on A during a
  transfer. It fits the Shape rule. Cost [I]: about 600–900 lines plus simulator models.
- **(c) Stock update mode: not viable.** The 1.2.1 package changes no companion on this device, because the SPI record matches [I]. The 1.2.5 package also
  flashes its bootloader and replaces XY Forever, so the dev menu and XY Forever are lost for good. **(d) Nothing:** 1.2.5 runs without the companion fixes;
  this is the backplane default.
- **What (b) needs from the builder:** allow the 4 UART calls, and only in the companion feature. Every current forbidden target stays; add
  `11a09582 88a26e7e 88a26f5e 88a23a28 88a2366a 88a2374c 88a2383e 11a07412`. Read the key from `0x88a290ac` at run time; never copy it into git.
- **Checks around every write [D]:** the image's SHA-256 equals an official member above, inside a signature-checked `.tfw` (003); the silicon ID or BLE
  platform word, and the hardware revision, match; battery ≥ 50 % and USB power present; confirmed on the device; a journal in `/yaffs2/xy-forever/` written
  before DFU; the guard fed, and nothing resets or ends the session during a transfer; never the Nordic bootloader slots.
