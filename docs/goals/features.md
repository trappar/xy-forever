# XY Forever goals: features

Part of [XY Forever goals](../GOALS.md).

## Features

1. **Normal startup unchanged.** With no key held, the default firmware starts
   at close to stock speed.
2. **A pleasant UI.** It has the XY Forever logo and a clean full-screen menu.
   Hold PLAY at power-on to open it.
3. **Firmware library.** Several firmware versions and builds are stored on
   the device. The host tool and web app boot one once or set the default; the
   menu's safe mode boots stock without mods and restores them.
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
7. **Mods.** Manage native mod packages and turn individual mods on or off from the
   menu. Browser onboarding imports an Open-XY mod selection for XY Forever to rebuild;
   the device uses its own configuration and metadata.
8. **Update manager.** One place to update everything:
   - XY Forever itself;
   - firmware (official and open-xy builds);
   - individual mods;
   - companion chips (keyboard/backplane, Bluetooth);
   - content.

   It shows what is installed and what a package would change, then installs
   with confirmation and a way back. Later goal: a web app as package manager
   (over the serial protocol), with a package index and dependency rules.
9. **Uninstall.** Restore a stock OS 1.2.1 OP-XY from Settings or the web app. Restore official
   content and the root `os.ldr`, write and verify the boot partition, remove the
   `xy-forever/` folder and restart.
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
