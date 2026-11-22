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
  - **Boot time close to stock.** A full OS file (2.4 MB) has been read in
    about 0.5–0.7 s. The stock bootloader already sends the OS from A to B
    over the internal link on every boot, so that part costs the same as now.
  - **No embedded OS**: a default that can't boot opens the
    menu.
- **The two processors.** The OP-XY has two:
  - **A** owns storage.
  - **B** owns the screen, keys and USB.

  XY Forever runs on both and coordinates them over their internal link,
  which works at startup.
- **Stock code is a driver layer, nothing more.** The stock
  bootloader's code is still in memory when XY Forever runs. Its hardware
  drivers stay in use, each checksum-verified, called as plain functions from
  XY Forever's own loops:
  - storage, the USB controller, the link between the processors;
  - the display and keys;
  - writing the boot area;
  Native codecs decrypt and verify official firmware with the device's own keys; no keys are shipped.

  XY Forever does not run stock features, drive stock state machines or patch
  stock state. Stock MTP is abandoned because its interrupt handler blocks on storage without a timeout.
- **Booting a firmware.** A reads and checks the chosen file and sends it to
  B, then both processors start it.
- **Official update.** XY Forever opens the official `.tfw` and installs:
  - the OS, as a library entry;
  - companion firmware only as a deferred goal;
  - the content.

  It never installs the 1.2.5 bootloader, which has no dev menu. The real
  bootloader stays at 1.2.1 under all circumstances.
- **Updates.**
  - **Delivery.** Update packages reach the device through MTP or the serial
    protocol; a web app can automate this later. XY Forever keeps track of what
    is installed.
  - **Firmware and mods.** Each update adds a new library entry. The old one
    stays until you remove it, so you can always go back.
  - **Companion chips.** Programming is planned, deferred until everything else is stable. Stock
    update routines can reach SPI metadata and are not an approved driver layer.
  - **XY Forever itself.** The product is one image. A future two-part update design would use:
    - **A small, rarely changed starter** sits in the boot area. Its stock
      writer has two slots and saves the new version's header last, so a
      power cut during an update never leaves the device unbootable.
    - **The main XY Forever** as a file. It updates like any other library
      entry and keeps the previous version. If a new version fails, the
      starter falls back to the previous one.
- **Mods.** XY Forever assembles the selected native packages into a library entry,
  with its own `info.txt` and content lists. `config.txt` owns the editable selection
  and memory choice. The browser will offer onboarding from an Open-XY `layout.json`:
  review its selected mods against native packages, then rebuild through XY Forever.
  The firmware never reads or writes that external file. Importing a compiled Open-XY
  image as the user's migration path is outside this flow.
  - **Saved projects stay safe.** open-xy already writes a small "companion"
    file next to each project, recording which machine each number meant, so
    projects open correctly after the machine set changes.
