# Operating the device: host tool

Part of [Operating the device](../OPERATING.md).

The rules in [SAFETY.md](../SAFETY.md) are binding for everything here.

## Host tool `uv run xyf-host` (macOS `/dev/cu.usbmodem*`)

`uv run xyf-host --help` lists the commands, with examples; `uv run xyf-host COMMAND --help` gives a command's
flags. For `library-add --content DIR`: the release's official content; the entry gets `content.sha256` +
`firmware.version` and verified content blobs, which mod builds need of their base (list-only imports need
matching live content or existing verified blobs).

Commands with `--yes-*` are dry runs without that flag. The file commands below act at once.

`dev-a-rehearsal BUILD` (or `dev-a-rehearsal STOCK_OS_LDR --uninstall`) stages verified input on
B, transfers it to A's separate RAM, checks it on A and prints the admission digest. This developer
command writes no storage and never installs or resets. `--deadline-ms` bounds it (1..120000);
Ctrl-C cancels an intact session. Status remains responsive; a lost link stops without recovery.
Manual changes may touch `/yaffs2` except the protected marker and updater inbox in SAFETY.md.
Creating the report folder is a storage write, so it takes `--yes-write`:
`uv run xyf-host put-file some.txt /yaffs2/xy-forever/log/README.txt --yes-write`.

### Sound packs

Copy a sound pack `.tfw` to `/yaffs2/xy-forever/inbox/`. Select it in
Settings > Content packs > Inbox, or run `uv run xyf-host packs import NAME.tfw --yes`.
XY Forever decrypts the archive, checks every file's CRC-32 and SHA-256 (not the `.tfw` signature) and retains
the extracted pack. `packs enable ID --yes` (or the menu) enables it, noting that it is not tested
on this firmware, and `packs disable ID --yes` retains it again. Enabling moves the pack folder only; getting its
sounds into the OS is #188. `packs forget ID --yes` deletes a retained pack, its records and the blobs no
other pack or firmware uses, so it can be imported again.

When an import ends, `packs import` prints where its time went on B (`packs.manage` operation 6): the phases
(admit, extract, finish), then extraction by what B did: `source` and `put` are B waiting for A's
reads and writes, `member` A's creates and closes, `aes`, `inflate` and `sha` B's own work, `service` its link and
USB turns, `loop` the menu loop between steps.

`uv run xyf-host packs list` lists the sound packs in `/yaffs2/content/dlc/` (state `active`) and the retained
imported packs in `/yaffs2/xy-forever/packs/` (state `retained`); an enabled one is `active`. An imported pack's
records are in `/yaffs2/xy-forever/pack-records/ID/`, outside the folder the OS reads. Each row gives the pack folder ID, its state and the
vendor manifest's name, version, author and description when present; an imported pack also shows `"imported": true` and
`"firmware": "not tested on this firmware"`.
A malformed manifest is reported beside its folder ID.

Place a complete, unpacked sound pack in `/yaffs2/xy-forever/inbox/ID/`. Its `manifest.json` must
be a JSON object of up to 4,096 bytes and 32 nesting levels. A `folder` property, when present, must match the folder ID exactly.
A manifest declaring the `xy-forever.package` schema belongs to the native package workflow.
Run `uv run xyf-host packs install ID --yes` to move the folder to `/yaffs2/content/dlc/ID/`.
An occupied ID stops the install. Use `uv run xyf-host packs remove ID --yes` to remove an installed pack.
Omit `--yes` to see the planned action.

On the OP-XY, open Settings > Content packs. SHIFT+PLAY switches between installed and inbox packs.
Use + and - to select a pack, then PLAY to open its install or remove confirmation. Cancel starts selected.
Use + then PLAY to confirm, or STOP to go back.

### Factory content inventory

`uv run xyf-host content status` reads free space, walks `/yaffs2/content/` and hashes each regular file
over USB. It compares the paths and SHA-256 digests with each available private
`xy-forever/content-manifests/<version>/content.sha256` (made with `uv run xyf-package content-manifest`).
The report gives every exact match, which releases match the live catalog hash, and missing, extra and changed files per release.
`matches_no_official_file` lists files whose path and digest match none of those releases.
Links and other special entries are reported separately. Keep the content folder unchanged during the scan.
A full factory set takes about 120 MiB of USB reads. A completed inventory exits successfully even with differences.

### File commands

Absolute device paths stay under `/yaffs2`; the device refuses changes to `/yaffs2/bootloader.dev`,
`/yaffs2/upgrade` and everything inside it (any ASCII case, also through links), and refuses removing
or renaming the root. The protected paths remain readable. Changes share the device's read-write
window, closed 8 s after the last change. `rm`, `mkdir`, `put`, `mv` and `cp` act at once.
`mv` renames or moves a file or folder; its target must not exist. `cp` copies on the device
(the bytes never cross USB); `cp -r` lists a folder and copies file by file to new names.

There is no guard (watchdog) and no command for one: a hang needs a power cycle; see
[SAFETY.md](../SAFETY.md). Never send REBOOT after suspecting a lone restart of A.

```sh
uv run xyf-host ls /yaffs2/xy-forever [--json]       # type, size, name (paged; any folder size)
uv run xyf-host get /yaffs2/xy-forever/log/x.bin x.bin   # any size; size (ls) and 4 ranges re-read
uv run xyf-host put x.bin /yaffs2/xy-forever/x.bin   # any size; SHA-256 checked on the device [--no-verify]
uv run xyf-host mkdir /yaffs2/xy-forever/new         # the parent must exist
uv run xyf-host rm /yaffs2/xy-forever/x.bin          # rm --dir: an empty folder only
uv run xyf-host mv /yaffs2/xy-forever/a /yaffs2/content/b   # file or folder; no overwrite (state)
uv run xyf-host cp [-r] /yaffs2/xy-forever/a /yaffs2/content/b   # <= 7 MiB per file; no overwrite (state)
uv run xyf-host mods apply --base opxy-121 --generation opxy-1.2.1/GEN fm2op [--ext ID...] --wait   # B assembles into tmp/job-N/
uv run xyf-host mods cancel                          # stop it (removes what it wrote)
```

### The mods the OP-XY runs (#26)

`mods status` prints the saved inputs (`config.get`). `mods set MOD... [--ext ID...] [--memory standard|expanded]
[--unverified]` saves a selection the way the Mods page does (`config.set`): the device reads its packages again,
checks that the selection can be built and keeps it, or refuses with the reason. `mods set` with no mods saves none.
Nothing is built then: the next boot of XY Forever converges to what is saved (#197). The web app's Mods page does the
same. Safe mode (Boot without mods) is a recovery action on the device only: a set sends it back as saved.

Developer path: `mods apply` and `scripts/test_entry.py --boot` (`set-default`, then `boot`) boot a firmware of your
choosing once, behind the saved inputs, which they never change. The next plain power-on finds the default differs
from its fingerprint and converges back to the saved setup (no `set-default` back needed). `mods dev` (#204,
[try.md](../mods-dev/try.md); `xyf dev` for mod writers) builds one mod alone and boots it once with its content, the default unchanged
(`menu.converge` mode 2); the next plain power-on finds the default's content missing and converges back.
