# Device safety (binding)

**Never brick the developer's OP-XY.** Any action that can change what the device boots
needs the highest bar of confidence. These rules are binding and override convenience.

## Never

- **Never write the real SPI bootloader.** It stays at 1.2.1. COM at power-on (dev
  menu, official 1.2.1 `.tfw`) then always remains a recovery path.
- **Never install the 1.2.5 bootloader.** Reasons (1000.0.23+0 vs 1.2.1's 1000.0.20+0):
  - Its dev-marker check (`0x88a1e0b0`) always returns 0, and the enabler command
    0xac is gone.
  - The new command 0x3a is a signed service unlock (P-224 ECDSA, `TEUNLOCK`).
  - Bootloader downgrade is rejected (`0x88a28e12`, status 7). It cannot be undone:
    teenage engineering's built-in updater cannot downgrade a 1.2.5 device either, so
    XY Forever does not support devices on 1.2.5.
- **Never run the official updater or TE tools near the device.** An unmodified
  official OS in the library keeps its own updater, which can stage a bootloader
  flash.
  - Only the 1.2.5 `os.ldr` goes into the library
    (record 003).
  - Do not install a full 1.2.5 OS without first proving that the OS can't trigger
    an upgrade, and then only with the developer's explicit yes.
- **Never program the companion chips** (keyboard/backplane PSoC, BLE).
- **Never write SPI metadata** (the record at SPI `0xc000`: upgrade marker, `factory`
  field).
- **Never write OTP.** XY Forever only reads it, on B, through the boot-ROM read `0x04000048`
  and init `0x04000054` exactly as the stock wrappers do (`tfw.check`, build 157). The builder
  admits no other ROM entry but memcpy `0x040201c4`; `0x0400004c` and `0x04000050` (never called
  by the 1.2.1 bootloader: unknown, possibly the program entry) are forbidden. Keys read on the
  device need no protection there; they never go into the repository.

- **Never let anything remove `/yaffs2/bootloader.dev`.** The 1.2.1 bootloader enables
  its dev menu (recovery step 2 below) when this file exists (`88a1d7e6`); no known
  method recreates it once XY Forever is gone too. The file engine refuses to remove or
  rename it away, on A for every request and on B before sending
  (`file_engine::decide_engine`, build 155; MTP mode's `mtp_protected_path` in builds
  136–154). Since build 187 it is also never written, replaced or
  renamed onto by hand (MTP, serial file commands); only A's own restore below re-creates it. Any future file feature (file
  write, uninstall, cleanup) must keep that rule. Spelling is not enough (yaffs follows
  links inside a path): since build 156 A also stats the marker around every unlink and
  rmdir, refuses a path that resolves to it, and re-creates (lowercase) a marker that
  existed before the operation and is gone after it; it never creates one that was absent.
- **Never let storage reach a boot partition by accident.** While install mode has a boot
  partition selected, A refuses every engine request, the read-write window and the state
  write; B selects partition 0 after every install, failed or not (build 156). A failed
  select of a boot partition counts as selected until a select of 0 succeeds (build 158).
- **Close the read-write window before any reset; never boot with it open** (a reset with it
  open is a power cut for yaffs). Every way out (reboot, the session's end; since 159
  also B's bootloader seen, B silent past A's idle window; since
  163 also 10 s without a request and every command item but reads and writes, and B closes it with
  op 9 before install, set default, CONTINUE and REBOOT) closes it first, from A's loop between
  requests or as op 9, never inside an executor. If that close
  fails, the window stays mounted and the restart paths still reset (by design: the next
  mount scans fully); CONTINUE never boots then. (A has no guard since 164.)

- **What may be changed by hand** (MTP and the serial file commands; build 187, protocol 1.13): everything on
  `/yaffs2` is writable, `user/` and the root included, except (1) `/yaffs2/bootloader.dev`: never
  changed, replaced, renamed or deleted, nothing renamed onto it; (2) `/yaffs2/upgrade/` (the stock
  updater's inbox): nothing written, created, renamed into or deleted inside it, `upgrade` itself never
  removed or renamed, no folder renamed to it. Both stay readable; both names any ASCII case.
  Enforced by `file_engine::decide_engine` on B before sending and on A for every request, by A's
  `reaches_protected` (a change whose path, or any folder above it, stats to either: links and `..`
  cannot escape), by the MTP responder (`xyf_core::mtp::writable`: ProtectionStatus 1, refusals) and
  early in the clients (`xyf_host.in_write_area`, `clients/ts inWriteArea`). XY Forever's own jobs keep
  their narrower rules (the content job: `storage_server::own_area`, never `user/`). Before changing
  `content/op1_factory.json`, save the device's copy first.

The "never program the companion chips" and "never write SPI metadata" rules give
way only to the developer's explicit, separate yes for that exact operation.

## What MTP mode may do

**Allow anything that can be recovered from without external hardware.** We are not
here to protect users from themselves, only from bricking the device. Apply this test
to every feature, not only MTP.

MTP behaves like the stock dev-menu MTP for every file-level request: users may
delete or overwrite anything, including XY Forever's own files, and recover through
the dev menu. Do not add protective refusals for convenience. What stays closed:
the raw partition layer (boot partition; MTP never sends it), the real bootloader
(no SPI write is linked; the stock upgrade resume is gated by the SPI marker
`0xab0d`, not by files), and removing the dev-menu marker above.

## Host access

Host tools reach the device only through XY Forever's own USB service (`xyf_host.py` and the
scripts built on it), and through the stock dev menu by the developer's hand for recovery. One
exception, also before the grant below: host tools may read files over the stock dev-menu MTP, never
write (`xy_forever/mtp_read.py` refuses every other operation; ONBOARDING step 4). No
flashing tools, no official updater, no TE tools. An agent operates a developer's OP-XY only after
that developer has granted it for their own device (ONBOARDING step 6, recorded in their local
`docs/status/`). The grant covers XY Forever's USB service under these rules, nothing else.

## Before installing any build over USB

1. It passes `xyf_build.py --verify`, the full test suite, and every CRC and call
   audit (the build runs them).
2. It is an XY Forever `os.ldr` that keeps the hold-COM recovery and the normal
   boot path intact.
3. Its install path is the stock commit-last A/B writer (`mmc_tools` `88a06a7e`,
   Track 5), verified by reading it back.
4. It carries one understood change, tested offline. Never batch untested risky
   changes into one install.
5. Tell the developer at the device before every device-changing action: install, reboot
   into a new build, storage write. Routine USB installs may run on that developer's
   standing yes. Ask first for new or risky behaviour, such as storage writes or new
   every-boot paths.
6. Read the reports afterwards, and stop at the first unexpected result.

If any check is uncertain, do not install. Ask the developer, and prepare a dev-menu kit
instead.

## Recovery ladder

1. **XY Forever's USB service:** reinstall a build, or `uninstall` to stock 1.2.1.
2. **Dev menu.** The developer holds COM at power-on, then Track 5 installs `/os.ldr`.
   Kits (`RECOVERY` in `xy_forever/paths.py`):
   - `known-good-NNN/os.ldr` (your current known-good build; keep a few older kits)
   - `stock-1.2.1/os.ldr` (stock)
   - Procedure: [OPERATING.md](OPERATING.md#dev-menu).
3. **Official 1.2.1 `.tfw` through the normal updater** (proven once after a dev-MTP
   crash; the dev bootloader survived). It may rewrite the companions with their
   1.2.1 versions. File: the official `opxy_firmware_1_2_1.tfw`, kept in the private
   `firmware/` folder.

Keep a known-good build and its dev-menu kit ready at all times.

## Lessons that cost a device session

- **Test 045 reset loop:** hold and guard left on with no host gave a 30 s
  self-reboot, forever.
  - Cause: the watchdog stayed armed through the stock reset, and the reset missed
    B.
  - Fixed in 046–047: `reset_self` disarms first; a held restart with no HELLO
    boots normally; held restarts wait 30 s for the host.
  - Rule: never leave hold or guard on with no host attached.
- **Prevent hangs; do not recover from lone restarts.** Effort goes into preventing hangs
  (time-limited waits, simple main loops), not into recovering from one processor crashing or
  restarting alone. A restart of B completes only if A restarts at about the same time or after
  it (record 022); every lone-restart recovery tried (builds 159-163) froze the device. Since 164
  B never resets itself on a lost link: the menu stays with USB, commands that need A answer
  `link`, and a power cycle recovers. A real hang needs a power cycle; that is accepted. Do not
  propose new device experiments for lone-restart recovery; prefer removing such machinery.
- **The stock Track-5 writer** erases the old partition's block 0 even when its own
  verify fails. It retries forever if A leaves mid-install. Test 034 hung on the
  300 s session cap.
- **USB:**
  - Replies of 4 KiB hung B, so ranged reads are capped at 1 KiB.
  - `begin()` refuses session caps over 600 s.
  - The stale stock CDC callback (`11a03e04`) drained the host's HELLOs until it was
    detached (Test 032).
- **Register reads can hang B (Test 095):** one PEEK of SEC SCTL/SSTAT for
  sources 0..127 hung B (no guard; power cycle needed). The BF707 most likely does not
  implement the upper sources. Read only registers the stock bootloader itself uses
  (`mmr_allowed` in `target/usb_service.cpp`), start with the smallest read, and arm
  the guard before anything new.
- **No embedded OS (since 088):** a boot whose link fails no longer falls back to
  a working OS; B shows "Internal link lost" and both halt until a power cycle.
  A build that breaks the link on every boot can only be recovered through the dev
  menu, so qualify every build (warm checks, a real boot, a PLAY power-on) before
  making it the kit.
- **Clock overflow (fixed in 086):** the cold ms formula overflowed after ~524 s;
  every timeout across that point was wrong. Use `xyf_clock.hpp` for all timing.
- **Module size:** a change that fits the product can push another mode past its
  reservation (084 broke `--xyf-mtp` by 272 bytes). Run `scripts/neutral.py` (it
  builds every mode) after shared-code changes.
- **Fast power cycles** skip the stock USB start. Wait about 5 s after any power-off.
- **L1 instruction SRAM:** data loads from it stall the boot at the logo. Tests 002,
  005 and 006 did this; 003, 004 and 007 did not.
- **Never leave the device in the OS** unnecessarily. Booting the OS is one-way, and
  only a power cycle at the device returns. End sessions held by keepalive, and use dry
  runs to test boot paths.
