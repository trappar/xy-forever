# Operating the device: host tool

Part of [Operating the device](../OPERATING.md).

The rules in [SAFETY.md](../SAFETY.md) are binding for everything here.

## Host tool `xyf_host.py` (stdlib; macOS `/dev/cu.usbmodem*`)

`wait`, `status`, `read PATH [--all --out F]`, `install BUILD --yes-install`,
`reboot --yes-reboot [--to service|os|menu]`, `set-default ID --yes-write`,
`put-file LOCAL /yaffs2/xy-forever/PATH --yes-write` (creates missing folders),
`library-add ID OS_LDR --name --version [--content DIR] --yes-write` (`--content`: the release's official content;
the entry gets `content.sha256` + `firmware.version` and verified content blobs, which mod builds need of their base;
list-only imports need matching live content or existing verified blobs), `screenshot --out F.png`,
`library-remove ID --yes-write`, `library-gc --yes-write`, `continue [--dry-run]`, `boot ID --yes-boot` (1.7: boot a library entry once; leaves XY
Forever), `key K`, `hold
on|off`, `uninstall STOCK_OS_LDR --yes-uninstall`.

Commands with `--yes-*` are dry runs without that flag. The file commands below act at once.

`dev-a-rehearsal BUILD` (or `dev-a-rehearsal STOCK_OS_LDR --uninstall`) stages verified input on
B, transfers it to A's separate RAM, checks it on A and prints the admission digest. This developer
command writes no storage and never installs or resets. `--deadline-ms` bounds it (1..120000);
Ctrl-C cancels an intact session. Status remains responsive; a lost link stops without recovery.
Manual changes may touch `/yaffs2` except the protected marker and updater inbox in SAFETY.md.
Creating the report folder is a storage write and requires the developer's approval:
`python3 xyf_host.py put-file some.txt /yaffs2/xy-forever/log/README.txt --yes-write`.

`python3 xyf_host.py packs list` lists installed sound packs in `/yaffs2/content/dlc/`.
Each row gives the pack folder ID and the vendor manifest's name, version, author and description when present.
A malformed manifest is reported beside its folder ID.

Place a complete, unpacked sound pack in `/yaffs2/xy-forever/inbox/ID/`. Its `manifest.json` must
be a JSON object of up to 4,096 bytes and 32 nesting levels. A `folder` property, when present, must match the folder ID exactly.
Run `python3 xyf_host.py packs install ID --yes` to move the folder to `/yaffs2/content/dlc/ID/`.
An occupied ID stops the install. Use `python3 xyf_host.py packs remove ID --yes` to remove an installed pack.
Omit `--yes` to see the planned action.

On the OP-XY, open Settings > Content packs. SHIFT+PLAY switches between installed and inbox packs.
Use + and - to select a pack, then PLAY to open its install or remove confirmation. Cancel starts selected.
Use + then PLAY to confirm, or STOP to go back.

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
python3 xyf_host.py ls /yaffs2/xy-forever [--json]       # type, size, name (paged; any folder size)
python3 xyf_host.py get /yaffs2/xy-forever/log/x.bin x.bin   # any size; size (ls) and 4 ranges re-read
python3 xyf_host.py put x.bin /yaffs2/xy-forever/x.bin   # <= HELLO stage= (7 MiB); read back [--no-verify]
python3 xyf_host.py mkdir /yaffs2/xy-forever/new         # the parent must exist
python3 xyf_host.py rm /yaffs2/xy-forever/x.bin          # rm --dir: an empty folder only
python3 xyf_host.py mv /yaffs2/xy-forever/a /yaffs2/content/b   # file or folder; no overwrite (state)
python3 xyf_host.py cp [-r] /yaffs2/xy-forever/a /yaffs2/content/b   # <= 7 MiB per file; no overwrite (state)
python3 xyf_host.py mods apply --base opxy-121 --generation opxy-1.2.1/GEN fm2op [--ext ID...] --wait   # B assembles into tmp/job-N/
python3 xyf_host.py mods cancel                          # stop it (removes what it wrote)
```
