# Operating the device: host tool

Part of [Operating the device](../OPERATING.md).

The rules in [SAFETY.md](../SAFETY.md) are binding for everything here.

## Host tool `xyf_host.py` (stdlib; macOS `/dev/cu.usbmodem*`)

`wait`, `status`, `read PATH [--all --out F]`, `install BUILD --yes-install`,
`reboot --yes-reboot [--to service|os|menu]`, `set-default ID --yes-write`,
`put-file LOCAL /yaffs2/xy-forever/PATH --yes-write` (creates missing folders),
`library-add ID OS_LDR --name --version --yes-write`, `screenshot --out F.png`,
`continue [--dry-run]`, `boot ID --yes-boot` (1.7: boot a library entry once; leaves XY Forever), `key K`, `hold
on|off`, `guard on|off|expiry-test` (processor B only),
`test-hang`,
`uninstall STOCK_OS_LDR --yes-uninstall`.

Commands with `--yes-*` are dry runs without that flag. The file commands below act at once.
Manual changes may touch `/yaffs2` except the protected marker and updater inbox in SAFETY.md.
Creating the report folder is a storage write and requires the developer's approval:
`python3 xyf_host.py put-file some.txt /yaffs2/xy-forever/log/README.txt --yes-write`.

### File commands

Absolute device paths stay under `/yaffs2`; the device refuses changes to `/yaffs2/bootloader.dev`,
`/yaffs2/upgrade` and everything inside it (any ASCII case, also through links), and refuses removing
or renaming the root. The protected paths remain readable. Changes share the device's read-write
window, closed 8 s after the last change. `rm`, `mkdir`, `put`, `mv` and `cp` act at once.
`mv` renames or moves a file or folder; its target must not exist. `cp` copies on the device
(the bytes never cross USB); `cp -r` lists a folder and copies file by file to new names.

`guard on`, `guard expiry-test`, `test-hang` and `--guard-test` must not run on the device.
The guard does not recover hangs; see [SAFETY.md](../SAFETY.md). Never send REBOOT after
suspecting a lone restart of A.

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
