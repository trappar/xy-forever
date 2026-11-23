# XY Forever storage layout: the folder

Part of the [XY Forever storage layout](../STORAGE.md).

Everything XY Forever owns lives under `/yaffs2/xy-forever/` (processor A's yaffs volume). Nothing outside
it is changed, except the boot partition (`os.ldr`) and the components an official update names.

```
/yaffs2/xy-forever/
  state-a.txt, state-b.txt  boot state, two power-safe slots (below)
  state.txt                 legacy single slot (read if no A/B slot is valid)
  firmware/<id>/os.ldr      one library entry's firmware image
  firmware/<id>/manifest.json   its description (legacy fallback: info.txt)
  firmware/<id>/files.sha256, content.sha256 + firmware.version   optional lists
  mods/<generation>/store.txt, {base,<machine>,ext-<extension>}/<file>   mod packages, flat (below)  [host-made]
  log/                      device reports, prod-a.bin / prod-b.bin, pfile-a.bin / pfile-b.bin
  stage2/main.xys2          stage 2: B loads it at the first HELLO of a boot; see below
  inbox/                    MTP drop folder for sound packs
  packs/<id>/               retained imported pack: the pack folder alone (enabled: content/dlc/<id>/)
  pack-records/<id>/        its records, retained or enabled: xyf-pack.txt (version, declared digest), xyf-pack-v1.sha256
  tmp/job-<id>/             mods.apply's output until the commit; -old: the replaced entry; swept (below)
  tmp/pkg-v1-<token>/       host package transaction; -old: detached old package, both swept
  tmp/adopt-<id>/           the stock entry's adoption until its rename into firmware/ (#178; jobs.md); swept by the next one
  blobs/<sha256>            verified content referenced by firmware and imported sound packs (package-transactions.md)
  tmp/put-<size>-<tag>      an upload (file.write_*, MTP SendObject) until A renames it onto its name; swept (below)
  job.txt, job.old           version-1 content intent; menu/service replay or previous-entry undo (jobs.md)
  content-state.txt         what /yaffs2/content/ matches, and what XY Forever installed there (below)
  content-official/         the official op1_factory.json and content.sha256, saved before the first content change
  config.json               the saved configuration: base, mods on, what Open-XY was built from (below)
  converged.txt             format=1, inputs=<SHA-256 of the saved configuration>, default=<id>: the last convergence (#197; state.md)
  settings.txt              device settings (#21, the Settings page; target/xyf_settings.hpp), format=1: power_on=play|always, usb=mtp_serial|mtp_only|serial (#191: serial or MTP off, live; usb=mtp: mtp_serial), confirm=0|1 (Ask before USB control, #65: no USB command changes it; a file transfer can); absent, unreadable or a missing key: the defaults; a newer format: the defaults, never rewritten
```

Changes by hand (MTP, serial file commands; protocol 1.13) may touch all of `/yaffs2` except
`bootloader.dev` and `upgrade/` (the stock updater's inbox), both readable, never changed, renamed or
deleted, nothing written into or renamed onto either. Details and XY Forever's own narrower rules:
[SAFETY.md](../SAFETY.md).

The reporter never creates `log/` (create it over USB first). Report names carry no build: the next
boot of any build overwrites them. B's USB service block inside says which release version and image
wrote it (`uv run -m xy_forever.census`: `usb_service.build`; the image only when the menu ran that boot).
Builds before #107 wrote `prod-NNN-{a,b}.bin`.

`<id>`: 1–31 characters, lowercase letters, digits, `-` and `_`; also the folder name.

**Depth** (#124; protocol 1.16): no file more than 6 folders below `/yaffs2` (`xy-forever/wt/a/b/c/d/x.txt`
is the deepest). The stock dev-menu MTP walks `/yaffs2` with 10-entry stacks (`889f1ff6`/`889f2200`/`889f22d8`).
On the device, after dev MTP has walked a file 7 folders deep (it lists, copies and deletes it), Track 5 in
that boot hangs at "looking for /os.ldr" and the next boot needs a storage repair; 8 cannot be read or
deleted, 9 crashes dev MTP at start. XY Forever refuses a deeper folder,
write, copy or move (`file_engine::decide_engine`; `file_client::rename` walks a folder moved deeper; the MTP
responder's `within_depth`; status `refused`); the host tools check first (`xy_forever/depth.py`,
`clients/ts/src/depth.ts`). Reads, listing and deletes work at any depth: delete an older deeper tree through
XY Forever's file service (`uv run xyf-host rm`, `mods remove`), never through dev MTP. The deepest paths XY
Forever makes: an entry's content, `tmp/job-<id>-old/content/presets/<id>/<file>` and
`firmware/<id>/content/presets/<id>/<file>` (6, the OS's own layout below the entry); mod packages sit flat,
`mods/<generation>/<package>/<file>` (4: [the store](mods-store.md)).
