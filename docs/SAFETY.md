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
  - Only the 1.2.5 `os.ldr` goes into the library.
  - Do not install a full 1.2.5 OS without first proving that the OS can't trigger
    an upgrade, and then only with the developer's explicit yes.
- **Never program the companion chips** (keyboard/backplane PSoC, BLE).
- **Never write SPI metadata** (the record at SPI `0xc000`: upgrade marker, `factory`
  field).
- **Never write OTP.** XY Forever reads on A or B through boot-ROM read `0x04000048`
  and init `0x04000054` as stock does (`tfw.check[_role]`, #15). The builder
  admits no other ROM entry but memcpy `0x040201c4`; `0x0400004c` and `0x04000050` (never called
  by the 1.2.1 bootloader: unknown, possibly the program entry) are forbidden. Keys read on the
  device need no protection there; they never go into the repository.

- **XY Forever never removes `/yaffs2/bootloader.dev`.** The 1.2.1 bootloader enables
  its dev menu (recovery step 2 below) when this file exists (`88a1d7e6`). te-boot re-creates it
  over Web MIDI on the 1.2.1 bootloader (command `0xac`, Ferret's enabler; the web app's Install page
  sends it, #178), so the developer may switch the dev menu off for a test (a retail-like start); the
  1.2.5 bootloader has no such command. No product feature removes it or turns the dev menu off.
  The file engine refuses to remove or
  rename it away, on A for every request and on B before sending
  (`file_engine::decide_engine`). It is also never written, replaced or
  renamed onto by hand (MTP, serial file commands); only A's own restore below re-creates it. Any future file feature (file
  write, uninstall, cleanup) must keep that rule. Spelling is not enough (yaffs follows
  links inside a path): A also stats the marker around every unlink and
  rmdir, refuses a path that resolves to it, and re-creates (lowercase) a marker that
  existed before the operation and is gone after it; it never creates one that was absent.
- **Never let storage reach a boot partition by accident.** While install mode has a boot
  partition selected, A refuses every engine request, the read-write window and the state
  write; B selects partition 0 after every install, failed or not. A failed
  select of a boot partition counts as selected until a select of 0 succeeds.
- **Close the read-write window before any reset; never boot with it open** (a reset with it
  open is a power cut for yaffs). Every way out closes it first,
  from A's loop between requests or as op 9, never inside an executor: reboot, the session's end,
  B's bootloader seen, B silent past A's idle window, 10 s without a request and every command
  item but reads and writes. B closes it with op 9 before install, set default, CONTINUE and REBOOT. If that close
  fails, the window stays mounted and the restart paths still reset (by design: the next
  mount scans fully); CONTINUE never boots then. A silent B only ends A's session: no restart.

- **What may be changed by hand** (MTP and the serial file commands; protocol 1.13): everything on
  `/yaffs2` is writable, `user/` and the root included, except (1) `/yaffs2/bootloader.dev`: never
  changed, replaced, renamed or deleted, nothing renamed onto it; (2) `/yaffs2/upgrade/` (the stock
  updater's inbox): nothing written, created, renamed into or deleted inside it, `upgrade` itself never
  removed or renamed, no folder renamed to it. Both stay readable; both names any ASCII case.
  Enforced by `file_engine::decide_engine` on B before sending and on A for every request, by A's
  `reaches_protected` and `final_link_reaches_inbox` (writable opens)
  ([how](hardware/storage-server.md#protected-inbox-paths)), by the MTP responder (`xyf_core::mtp::writable`:
  ProtectionStatus 1, refusals) and early in the clients (`xyf_host.in_write_area`, `clients/ts inWriteArea`).
  XY Forever's own jobs keep their narrower rules (the content job: `storage_server::own_area`, never
  `user/`). Before changing `content/op1_factory.json`, save the device's copy first.
- **Depth** (#124): no file over 6 folders below `/yaffs2`; deeper hangs Track 5 after dev MTP.
  Deletes work at any depth ([details](storage/layout.md)).

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

Host tools reach the device via XY Forever's USB service (`xyf_host.py`), standard MTP (stock OS,
stock dev menu, XY Forever's MTP mode), and the dev menu by hand (recovery). Over MTP they may
write under the rules above (protected names, depth); test files go in a scratch folder, removed
after and [logged](operating/at-the-device.md); nothing else is deleted. Before the grant, only dev-menu
MTP reads (ONBOARDING step 4). No flashing tools, no official updater, no TE tools. An agent operates a
developer's OP-XY only after that developer has granted it for their device (ONBOARDING step 6,
recorded in their `docs/status/`). The grant covers these routes and rules, nothing else.

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
   - `known-good-<image>/os.ldr` (your current known-good build, by its image; keep a few older kits)
   - `stock-1.2.1/os.ldr` (stock)
   - Procedure: [operating/at-the-device.md](operating/at-the-device.md#dev-menu).
3. **Official 1.2.1 `.tfw` through the normal updater** (proven once after a dev-MTP
   crash; the dev bootloader survived). It may rewrite the companions with their
   1.2.1 versions. File: the official `opxy_firmware_1_2_1.tfw`, kept in the private
   `firmware/` folder.

Keep a known-good build and its dev-menu kit ready at all times.

## Current rules from device failures

- **There is no guard (watchdog).** No processor arms WDOG0, and no host command or pass step
  turns one on: `diag.guard` (0x33) and `diag.hang` (0x60) are retired since protocol 1.14. Nothing
  recovers a hang but a manual power cycle: stop on a suspected hang and request one. Restarting B
  alone freezes the device; never send REBOOT after a suspected lone A restart. Builds before 1.14
  still answer `diag.guard`: never send it to them either.
- **A fault on B while its menu has the screen** shows the crash screen (cause, addresses) and B halts until a
  power cycle; no reset, no storage write (#102, `key_ui_crash.ipp`). Outside the menu the stock vector runs.
- **Never leave hold on with no host attached.** Hold and the old guard together caused a
  repeated self-reboot. A held restart with no HELLO boots normally; held restarts wait 30 s
  for the host.
- **Prevent hangs; do not recover from lone restarts.** Use time-limited waits and simple main loops.
  A restart of B completes only if A restarts at about the same time or after it. B never resets
  itself on a lost link: the menu stays with USB, commands that need A answer `link`, and a power
  cycle recovers. A real hang needs a power cycle; that is accepted. Do not propose new device
  experiments for lone-restart recovery; prefer removing such machinery.
- **The stock Track-5 writer** erases the old partition's block 0 even when its own verify fails.
  It retries forever if A leaves mid-install. Check stock return conventions before any boot write.
- **USB:** replies of 4 KiB hung B, so ranged reads are capped at 1 KiB. `begin()` refuses session caps
  over 600 s. Detach the stale stock CDC callback `11a03e04`, which drains the host's HELLOs.
  Start USB once per boot as one composite device; stopping and restarting functions inside a boot
  froze B. Compare descriptor layouts against stock, including the full 0x18-byte config record.
- **Register reads can hang B:** SEC SCTL/SSTAT reads over sources 0..127 hung B; the BF707 most
  likely does not implement the upper sources. Read only registers the stock bootloader itself uses
  (`mmr_allowed` in `target/usb_service_files.ipp`). Any new probe needs review and bounded operations:
  nothing but a power cycle recovers a hung B.
- **No embedded OS:** a boot whose link fails shows "Internal link lost" and both halt until a power
  cycle. A build that breaks the link on every boot needs dev-menu recovery: qualify warm checks,
  a real boot and a PLAY power-on before making it the known-good kit.
- **Timing:** use `xyf_clock.hpp` for wrap-safe timing. Size boot waits for a full yaffs scan, which
  can take tens of seconds after a dev-menu MTP copy; do not assume the fast checkpoint mount.
- **Module size:** shared code can push another mode past its reservation. Run `scripts/neutral.py`
  (it builds every mode) after shared-code changes.
- **Fast power cycles** skip the stock USB start. Wait about 5 s after any power-off.
- **L1 instruction SRAM:** data loads from it stall the boot at the logo; never read it as data.
- **Never leave the device in the OS** unnecessarily. Booting the OS is one-way; only a power cycle
  at the device returns. End sessions held by keepalive, and use dry runs to test boot paths.
