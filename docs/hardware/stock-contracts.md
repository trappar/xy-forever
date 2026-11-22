# Hardware and stock-code facts: stock contracts

Part of [Hardware and stock-code facts](../HARDWARE.md). The storage requests themselves:
[stock storage server requests](storage-server.md).

## Stock contracts

- **Restart order (stock boot, read from the listing):**
  - B boots A, not the reverse: early in B's bootloader (`88a1d34c`, before the display reset,
    USB and the link) B sends A `0x03`. A fresh A (boot ROM, `0xff`) gets the bootloader pushed
    and both go on; a running A (its bootloader or XY Forever) makes B wait for PA6 with no
    bound. So a lone B reset freezes (old frame on screen) until A resets; REBOOT works
    because A resets ~0.1 s first. XY Forever never resets B unless A follows or went first.
  - Both processors run the same main (`88a35668`) and boot routine (`88a34a00`). A
    (role 0) first serves in `88a35b90`: it polls until B's release (op 7) arrives, with
    no timeout, then reads `os.ldr` itself and boots. So A may restart long before B.
  - B's fetch `88a07240`: select partition 1 (op `0x18`); a select that returns an error
    makes the function return that error as the length, and B then "boots" whatever is
    at `0x8a000000`. A sector read (op `0x11`) that fails is retried forever.
  - B's request call (`889f869e` -> `889f7b12`) waits for the done flag with no timeout.
    XY Forever never calls it: B's file client builds the same requests
    with the stock builders and polls the done flag itself, with a bound (`file_client.cpp`).
    A request that the running A took and never answered is answered by A's bootloader
    after A restarts. So in any mode A must never ANSWER a
    restarted B's boot requests: leave them and restart A. In every mode, B's link open (op 10 `0x12345678`) after its first command,
    or a boot request (select, sector read) with nothing frozen outside install mode, is held
    unanswered, A closes storage and restarts; a B that loses A at the menu stays there.
  - The stock select `88a08710` uses only the argument's low 3 bits and passes them with
    `0xb3` to `88a0826c` [I: eMMC EXT_CSD 179, partition access]: a command request (`'CM'`
    tag) that reaches A's bootloader selects partition `value & 7`. B's command values keep
    those bits 0 (the user area).
  - The master queues requests (priority list at driver `+0xac`, active at `+0xb0`), but
    a request and its data body are not one unit: a second request submitted between
    them breaks the exchange (146). Only submit while no stock call can be in progress
    (B's main loop is safe; an interrupt above the USB interrupt is not).
  - A stock flag `B[0x88a1cdc1]` (set by the inter-CPU command handler `0x88a5a454`)
    makes a bootloader reset itself; unused by XY Forever.
- **Timing diagnostics:** `diag.mtp` op 7 admits A's normal read service before reading or clearing
  its counters: a catalog refresh leaves A outside file RPC, where a raw timing request would remain
  unanswered. Busy jobs, uploads, MTP operations and window closes exclude these queries. An outstanding
  file request keeps its slot; unavailable counters never cancel it or reset a processor.
- **WDOG0 as an interrupt (SEC source 3;** found with the removed guard, unused now): the expiry flag
  WDRO (`WDOG_CTL` bit 15, write 1 to clear) clears only while the watchdog is disabled. Write the disable value first,
  then the disable value with bit 15, then reload and enable. Clearing it in the disable
  write leaves the request up: the handler re-enters forever as soon as it returns.
- **The display keeps its last frame** when B resets or hangs: a frozen screen does not
  tell which.
- **Dev-menu marker:** the file `/yaffs2/bootloader.dev` (4 bytes). `88a1d7e6` checks that
  it exists; `88a1d7a2` (command `0xac`) creates or removes it and sets the dev flag
  `B[0x88a1f3be]`. See SAFETY.
- **Upgrade resume at boot** (`88a3512a` -> `88a28ff4` -> updater `88a2863c`) runs only when
  the SPI metadata word read by `11a05244` equals `0xab0d`. Files in `/yaffs2/upgrade/`
  alone do nothing. The bootloader's update mode takes a `.tfw` from a FatFs RAM disk over
  USB mass storage, never from yaffs.

