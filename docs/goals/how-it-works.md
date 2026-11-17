# XY Forever goals: how it works

Part of [XY Forever goals](../GOALS.md).

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
- **Mods.** XY Forever assembles the selected native packages into a library entry,
  with its own `info.txt` and content lists. `config.txt` owns the editable selection
  and memory choice. The browser will offer onboarding from an Open-XY `layout.json`:
  review its selected mods against native packages, then rebuild through XY Forever.
  The firmware never reads or writes that external file. Importing a compiled Open-XY
  image as the user's migration path is outside this flow (decision 035).
  - **Saved projects stay safe.** open-xy already writes a small "companion"
    file next to each project, recording which machine each number meant, so
    projects open correctly after the machine set changes.
