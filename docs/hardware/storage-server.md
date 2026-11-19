# Hardware and stock-code facts: stock storage server requests

Part of [Hardware and stock-code facts](../HARDWARE.md).

## Stock storage server requests (role 0, as MTP uses them)

Classes by vtable; A's executor runs the op and replies. **Some classes always send a
data body, and B waits for it without a timeout**, so a refusal must have the same shape.

| Class (vtable) | Ops | Reply body |
|---|---|---|
| partition `0x11907694` (`889f76ca`) | 8 mount, 9 unmount, `0x2c` lseek, `0x2d` close, `0x30` closedir, `0x34` fsync, `0x36` truncate (by descriptor); 4 / `0x14` / `0x18` = raw partition layer | none |
| path `0x119076b4` (`889f6fea`) | `0x21` access, `0x25` unlink, `0x26` mkdir, `0x27` rmdir, `0x28` yaffs sync, `0x29` open (flags at +8), `0x2e` opendir, `0x35` set times | none |
| query `0x11907388` (`889f72e4`) | `0x22` stat, `0x23` statfs, `0x31` free space (64-bit) | always: `0x34`, `0x0c`, 8 bytes |
| sector `0x11907348` (`889f6b7c`) | `0x2f` readdir (result: A's dirent address, 0 at the end, `889f6c02`; never 1); `0x11` / `0x12` boot-partition sector reads | always: `0x10c` (also at end of folder); `0x200`·n |
| read `0x11907368` (`889f6cd6`) | `0x2a` read | streamed |
| write `0x11907710` (`889f6dca`) | `0x2b` write; `0x15` / `0x16` boot-partition sector writes | none |

There is no rename and no format request. A's slave interrupt answers the link handshake
(ops 1 and 10) by itself in every mode, so a B that restarts alone talks to the old A.

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

## Protected inbox paths

A's mutation guard compares the marker's identity and walks the resolved parent directories of a path
before changing storage. This catches aliases directly into inbox subfolders, not just aliases to the
`upgrade/` root. YAFFS follows directory links before interpreting internal `..` (1.2.1 listing:
`889fa246` / `889fa258`; a final `..` uses the parent at `889fa300`). The guard uses the already-admitted
local stat call, never a new stock entry; client paths still reject literal `.` and `..` components.

The walk is bounded at 120 parent steps, enough for every direct client path's 239-byte limit. An alias
hiding deeper ancestry, a failed parent stat or a missing mount root refuses the mutation. This is not
the six-folder creation cap: existing deep files remain deletable when ancestry fits that bound.
The extra stats have not been timed on hardware.

**Remaining gap (#6):** a link directly to an inbox file exposes no parent through stat. The directory
walk cannot prove that file's containment. Simulator reproduction through the production staged
`file.write_staged` endpoint: a link under `xy-forever/log/` to `upgrade/sub/x.tfw` lets the write replace
the protected file's bytes (`usb_service_file_write.ipp`, `file_client::write_file` opens the final path).
The newer streaming upload and MTP use temporary files and rename; their final-path behavior does not
establish safety of this older endpoint. Full inbox protection remains an open review requirement;
the binding rule in [SAFETY](../SAFETY.md) is unchanged. No device experiment establishes this gap.
