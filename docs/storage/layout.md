# XY Forever storage layout: the folder

Part of the [XY Forever storage layout](../STORAGE.md).

Everything XY Forever owns lives under `/yaffs2/xy-forever/` (processor A's yaffs volume). Nothing outside
it is changed, except the boot partition (`os.ldr`) and the components an official update names.

```
/yaffs2/xy-forever/
  state-a.txt, state-b.txt  boot state, two power-safe slots (below)
  state.txt                 legacy single slot (read if no A/B slot is valid)
  firmware/<id>/os.ldr      one library entry's firmware image
  firmware/<id>/info.txt    its description (key=value, below)
  firmware/<id>/files.sha256, content.sha256 + firmware.version   optional lists
  mods/<profile>/<generation>/{base,<machine>,ext-<extension>}/   mod packages (below)  [host-made]
  log/                      device reports, prod-NNN-a.bin / prod-NNN-b.bin
  stage2/main.xys2          stage 2: B loads it at the first HELLO of a boot; see below
  inbox/                    MTP drop folder: .tfw, .ldr, open-xy bundles   [planned]
  tmp/job-<id>/             mods.apply's output until the commit; -old: the replaced entry; swept (below)
  content-state.txt         what /yaffs2/content/ matches, and what XY Forever installed there (below)
  content-official/         the official op1_factory.json and content.sha256, saved before the first content change
  config.txt                the saved configuration: base, mods on, what Open-XY was built from (below)
  settings.txt              device settings (#21, the Settings page; target/xyf_settings.hpp): confirm=0|1 stored for Confirm on device (not built); absent or unreadable: the defaults
```

Changes by hand (MTP, serial file commands; protocol 1.13) may touch all of `/yaffs2` except
`bootloader.dev` and `upgrade/` (the stock updater's inbox), both readable, never changed, renamed or
deleted, nothing written into or renamed onto either. Details and XY Forever's own narrower rules:
[SAFETY.md](../SAFETY.md).

The reporter never creates `log/` (create it over USB first). Report names include the running
firmware build number (`prod-NNN-{a,b}.bin`) so private evidence can identify its producer.

`<id>`: 1–31 characters, lowercase letters, digits, `-` and `_`; also the folder name.
