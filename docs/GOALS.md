# XY Forever: goals

XY Forever is a boot menu and firmware manager that runs before the OP-XY
software. Once it is installed, the user never needs the stock bootloader or
the dev menu again. Trying out firmware and mods should be easy.

It supports OP-XYs on 1.2.1 only: it is installed from the 1.2.1 bootloader's dev menu,
and a device updated to 1.2.5 cannot be downgraded with the built-in firmware updater.

## Features

1. **Normal startup unchanged.** With no key held, the default firmware starts
   at close to stock speed.
2. **A pleasant UI.** It has the XY Forever logo and a clean full-screen menu.
   Hold PLAY at power-on to open it.
3. **Firmware library.** Several firmware versions and builds are stored on
   the device. Pick one to boot once, or set it as the default.
4. **File access over USB.** One file engine on the device (list, read, write,
   delete, make folder) with two doors:
   - **our own MTP responder**: add, update or remove firmware, mods and other
     files with any MTP client. It replaces the dev-menu MTP mode. Leaving it
     returns to the menu without a restart;
   - **a published serial protocol**: commands, installs and automation.

   Firmware, mods and content packs are "put a file, send install" through
   either door, or installed from the on-device menu.
5. **Official updates without losing anything.** Installing an official
   firmware file such as 1.2.5 updates the OS, the companion chips and the
   content. XY Forever and the dev menu stay available.
6. **Everything the stock bootloader does,** including installing content
   packs (new in 1.2.5; a later goal).
7. **Mods.** Load open-xy builds, see which mods each contains (from
   `layout.json`), and turn individual mods on or off from the menu.
8. **Update manager.** One place to update everything:
   - XY Forever itself;
   - firmware (official and open-xy builds);
   - individual mods;
   - companion chips (keyboard/backplane, Bluetooth);
   - content.

   It shows what is installed and what a package would change, then installs
   with confirmation and a way back. Later goal: a web app as package manager
   (over the serial protocol), with a package index and dependency rules.
9. **Uninstall.** Restore a stock OP-XY of your chosen official version and
   delete all XY Forever files.
10. **Info.** Versions and storage use.
11. **Safety:**
    - **XY Forever is home.** If there is no bootable firmware anywhere on
      the device, or the chosen one is missing or fails its checks, XY
      Forever opens its menu. From there you can pick another firmware or
      use MTP mode to add one. Users should never need to go further than
      this.
    - **The real bootloader is the ultimate safety net.** It is never modified
      or updated past 1.2.1. Holding COM at power-on still allows
      reinstalling official 1.2.1, or using the dev menu if it was left
      enabled.
    - Images are checked before they boot.
    - Destructive actions ask for confirmation.

## How it works (simplified)

- **What stays and what changes.** The real 1.2.1 bootloader stays installed
  permanently and is never updated. It always loads `os.ldr` from the boot
  area, and that `os.ldr` is XY Forever.
- **XY Forever contains no OS.** On every boot it loads the chosen firmware
  from a file in `/yaffs2/xy-forever/`, where firmware versions, mods and settings
  all live.
  - **No writes on boot, so no wear.** Booting only reads the file. Storage is
    written only when you install, update or change a setting.
  - **Boot time close to stock.** Tests 014/015 read a full OS file (2.4 MB) in
    about 0.5–0.7 s. The stock bootloader already sends the OS from A to B
    over the internal link on every boot, so that part costs the same as now.
  - **No embedded OS** since build 088: a default that can't boot opens the
    menu.
- **The two processors.** The OP-XY has two:
  - **A** owns storage.
  - **B** owns the screen, keys and USB.

  XY Forever runs on both and coordinates them over their internal link,
  which Test 023 proved works at startup.
- **Stock code is a driver layer, nothing more.** The stock
  bootloader's code is still in memory when XY Forever runs. Its hardware
  drivers stay in use, each checksum-verified, called as plain functions from
  XY Forever's own loops:
  - storage, the USB controller, the link between the processors;
  - the display and keys;
  - writing the boot area;
  - decrypting and verifying official firmware files with the device's own
    built-in key, so no keys are shipped with XY Forever.

  XY Forever does not run stock features, drive stock state machines or patch
  stock state. Stock MTP was tried and dropped (HISTORY.md).
- **Booting a firmware.** A reads and checks the chosen file and sends it to
  B, then both processors start it.
- **Official update.** XY Forever opens the official `.tfw` and installs:
  - the OS, as a library entry;
  - the companion chip firmware (keyboard/backplane and Bluetooth);
  - the content.

  It never installs the 1.2.5 bootloader, which has no dev menu. The real
  bootloader stays at 1.2.1 under all circumstances.
- **Updates.**
  - **Delivery.** Update packages reach the device through MTP or the serial
    protocol; a web app can automate this later. XY Forever keeps track of what
    is installed.
  - **Firmware and mods.** Each update adds a new library entry. The old one
    stays until you remove it, so you can always go back.
  - **Companion chips.** These use the stock update routines, and only when a
    package changes them.
  - **XY Forever itself.** It comes in two parts:
    - **A small, rarely changed starter** sits in the boot area. Its stock
      writer has two slots and saves the new version's header last, so a
      power cut during an update never leaves the device unbootable.
    - **The main XY Forever** is a file. It updates like any other library
      entry and keeps the previous version. If a new version fails, the
      starter falls back to the previous one.
- **Mods.** Today an open-xy build is linked on a computer. Every chosen
  machine is built into one `os.ldr`, and dynamic loading isn't implemented.
  `layout.json` lists the machines and their engine/effect numbers.
  - **Step 1 (no open-xy changes).** Each open-xy build is a library entry,
    and XY Forever shows its machine list from `layout.json`.
  - **Step 2 (with the open-xy maintainers).** The builder also outputs each
    machine as a relocatable module, plus a base firmware and a link plan. XY
    Forever then links the enabled machines into the firmware in memory at
    boot. That's fast at 400 MHz and writes nothing.
  - **Saved projects stay safe.** open-xy already writes a small "companion"
    file next to each project, recording which machine each number meant, so
    projects open correctly after the machine set changes.

## Roadmap

The roadmap and its milestones are in [PLAN.md](PLAN.md) (after publication, work is
claimed through GitHub Issues); your device's state is in your local `STATUS.md` (copy
`STATUS.template.md`); lessons from device tests are in [HISTORY.md](HISTORY.md).

## Biggest unknowns

- **Running 1.2.5 on the 1.2.1 bootloader** works (Test 053, booted from the
  library); not exercised in depth.
- **Updating the companion chips from XY Forever** (keyboard/backplane 1.0.8,
  Bluetooth 1.0.2) is untested, and it carries the highest risk.
- **How 1.2.5 installs content packs** isn't investigated yet.
- **Writing to storage before the OS starts** works (Tests 048-050).
- **Self-update: checked offline.** The stock boot-area writer is safe against
  power loss. It can't fall back from a new version that is valid but broken,
  because slot 1 always wins. That is why XY Forever is split into a small
  starter and a main file.
- **Per-firmware content:** older firmware such as 1.1.33 boots but is largely
  broken without its own content (Tests 055/056).
- **A one-time storage scan:** the first mount after some changes can take a
  few seconds; it took 4.6 s once in Test 015. This needs a look for boot
  time.

## Decisions

- **The real bootloader stays at 1.2.1 forever.** Uninstall restores a
  stock OS but keeps the 1.2.1 bootloader.
- **No OS is embedded in XY Forever.** It boots firmware from a file every
  time.
- **If the default entry can't boot, XY Forever opens its menu**,
  like a normal bootloader/BIOS: the user picks firmware with the keys. No
  silent fallback to another entry. The USB service stays available for repair,
  and the stock dev menu (hold COM) remains the last resort.
- **Shape:** one file engine in XY Forever's main loops, with
  timeouts, behind two doors: our own MTP responder and a published serial
  protocol. Stock MTP and its watchdog recovery (builds 139–150) are abandoned.
- **Link failure:** no automatic restart scheme; fix the causes of
  link loss at their source. A boot that loads no image shows why and halts
  until a power cycle.
- **Boot transfers carry no SHA-256**: size and CRC-32 from
  `info.txt` identify the image.
- **MTP and file features:** allow anything recoverable without external
  hardware ([SAFETY.md](SAFETY.md)).
- **Companion chips are deferred** until everything else is stable; every
  operation on them needs the developer's explicit yes.
- **Logo:** a simple XY Forever logo, drawn for the project.

- **Protocol:** the specification is published in this repository; the screenshot
  command is a product command and is documented. Commands used only for testing
  (memory peek, key injection, hang test) stay in every build but are left out of the
  published documentation.
- **Destructive host commands:** whether they also need a key press on the OP-XY is a
  setting, changeable only on the device (PLAN 4.8). Web app (013): the confirmation is on
  by default (a test device may turn it off so tests run unattended); Chrome only.
- **Mods:** on the device you choose which mods are active and XY Forever sets up the
  firmware for that choice; inactive mods are not loaded and use no memory, so no
  enable table inside one big build (005, option b). We support the mods open-xy can
  load, loading them our own way with our own tooling (no collaboration needed to
  start). Packages are split from open-xy's kit by a host tool; the whole published
  catalog is offered; builds use the website's mode (per-voice measuring off) by default,
  the measuring build is a developer option. Extensions need at least one mod on; capture
  and the community DJ filter are both offered. Extensions and companion-chip updates are
  supported goals.
- **Mod trust:** known official base packages assemble normally; an unrecognised base
  only after an on-device confirmation. A mod set is built only on an OS image XY
  Forever verified itself from an official `.tfw`.
- **open-xy facts** come from a fresh copy of upstream, with its commit cited, never
  from a local working checkout.
- **One image, not two stages:** one larger image now; stage 2 only if needed later
  (then: 2 tries, the previous stage 1 kept, escape key PLAY + STOP).
- **Content:** XY Forever acts as a package manager for `content/`: it installs the
  official content folder for the firmware version, then the active mods' changes on
  top (a firmware-version change rebuilds that from a cache of the installed mods kept on
  the device), tracks what it installed and cleans up after itself; it may rewrite `content/`
  when the default entry changes, replacing it as stock does (keeping packs). No backup
  first: official content is re-creatable from the `.tfw`. Unknown files in `content/`
  are left and reported; a full reinstall removes them. Unsigned content packs (plain
  folders) may be installed.
- **Packages (010):** leaving a trial of an entry with other content is Undo in the
  menu, never a write during a normal boot; a `.tfw` is deleted after install; stock's
  `upgrade/` folder is never deleted automatically (its size is shown, removal offered).
- **No recovery for a lone-processor restart:** prevent hangs with bounded waits
  instead. A real hang needs a manual power cycle.
- **Keys on the device need no protection** (no peek exclusion, no key hiding in the
  firmware); keys stay out of the repository, commits and published files.
- **Not planned:** reading the owner's projects and samples from XY Forever (the OS's
  own MTP covers it; 011); a fix for projects that use a mod which is off (at most a
  notice before boot).
- **007's recommendations apply.** 009's six stock calls are allowed, on `/yaffs2` only.
  The first A-side boot write (012) risks a dev-menu reinstall at worst and needs the
  developer's explicit yes.
