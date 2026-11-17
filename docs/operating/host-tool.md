# Operating the device: host tool

Part of [Operating the device](../OPERATING.md).

The rules in [SAFETY.md](../SAFETY.md) are binding for everything here.

## Host tool `xyf_host.py` (stdlib; macOS `/dev/cu.usbmodem*`)

`wait`, `status`, `read PATH [--all --out F]`, `install BUILD --yes-install`,
`reboot --yes-reboot [--to service|os|menu]`, `set-default ID --yes-write`,
`put-file LOCAL /yaffs2/xy-forever/PATH --yes-write` (creates missing folders),
`library-add ID OS_LDR --name --version --yes-write`, `screenshot --out F.png`,
`continue [--dry-run]`, `boot ID --yes-boot` (1.7: boot a library entry once; leaves XY Forever), `key K`, `hold on|off`, `guard on|off|expiry-test` (processor
B only since 164),
`test-hang`,
`uninstall STOCK_OS_LDR --yes-uninstall`.

Every write or install is a dry run without its `--yes-*` flag. Writes are limited
to `/yaffs2/xy-forever/` and (build 168+) `/yaffs2/content/`. Example: creating the report folder before installing
build 067+ (a storage write, so ask the developer first):
`python3 xyf_host.py put-file some.txt /yaffs2/xy-forever/log/README.txt --yes-write`.

### File commands (build 165+, protocol 1.5)

Absolute device paths under `/yaffs2`; the device refuses `/yaffs2/bootloader.dev`, `/yaffs2`
itself and anything outside `/yaffs2`. Build 168+: `put`, `rm` and `mkdir` only under
`/yaffs2/xy-forever/` and `/yaffs2/content/` (no name holding `bootloader`). Changes go through the device's write window (closed
8 s after the last change). `rm`, `mkdir`, `put`, `mv` and `cp` act at once (no `--yes-*` flag). Build 169+:
`mv` renames or moves a file or a whole folder within those two folders; the target must not exist.
Build 170+: `cp` copies on the device (the bytes never cross USB) from any readable file to a new name
in those folders; `cp -r` copies a folder (the tool lists it and copies file by file).

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
