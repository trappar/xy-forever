# XY Forever goals: decisions

Part of [XY Forever goals](../GOALS.md).

## Decisions

- **Name:** XY Forever is a "firmware and mod manager" ("firmware & mod manager" only in
  headings and tight blurbs). Never call it a bootloader or boot manager: those terms stay
  with teenage engineering's stock bootloaders and the community's replacement-bootloader
  project, a different layer.
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
  protocol. Stock MTP and its watchdog recovery are abandoned.
- **Link failure:** no automatic restart scheme; fix the causes of
  link loss at their source. A boot that loads no image shows why and halts
  until a power cycle.
- **Boot transfers carry no SHA-256**: size and CRC-32 from
  `info.txt` identify the image.
- **MTP and file features:** allow anything recoverable without external
  hardware ([SAFETY.md](../SAFETY.md)).
- **Companion chips are deferred** until everything else is stable; every
  operation on them needs the developer's explicit yes.
- **Logo:** a simple XY Forever logo, drawn for the project.

- **Protocol:** the specification is published in this repository; the screenshot
  command is a product command and is documented. Commands used only for testing
  (memory peek, key injection, hang test) stay in every build but are left out of the
  published documentation.
- **Destructive host commands:** on-device confirmation policy remains undecided in
  [issue #56](https://github.com/trappar/xy-forever/issues/56); the setting is tracked in #21.
  The web app targets Chrome.
- **Mods:** on the device you choose which mods are active and XY Forever sets up the
  firmware for that choice; inactive mods are not loaded and use no memory, so no
  enable table inside one big build. We support the mods open-xy can
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
- **Packages:** leaving a trial of an entry with other content is Undo in the
  menu, never a write during a normal boot; a `.tfw` is deleted after install; stock's
  `upgrade/` folder is never deleted automatically (its size is shown, removal offered).
- **No recovery for a lone-processor restart:** prevent hangs with bounded waits
  instead. A real hang needs a manual power cycle.
- **Keys on the device need no protection** (no peek exclusion, no key hiding in the
  firmware); keys stay out of the repository, commits and published files.
- **Not planned:** reading the owner's projects and samples from XY Forever (the OS's
  own MTP covers it); a fix for projects that use a mod which is off (at most a
  notice before boot).
- **Stock code stays a driver layer.** File-engine stock calls are scoped and audited; see
  [code rules](../architecture/code-rules.md). A first A-side boot write needs the developer's
  explicit yes and a dev-menu recovery kit.
