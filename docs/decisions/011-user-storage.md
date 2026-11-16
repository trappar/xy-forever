# 011: Where the OS keeps projects and samples (plan item 2.7)

Research record, 2026-11-14; no device, no build. **[L]** read from a listing or image (1.2.1 bootloader `bootloader-121-listing`; OS `xy-forever-125-002/norm-os{121,125}.asm`, strings from the `os.ldr` images), **[F]** read from a doc, report or git history, **[M]** measured on files, **[I]** inferred, **[U]** unknown. OS addresses are 1.2.1.

**Question:** what the OS's `/fat32` is, where it lives, what each MTP mode shows, and whether XY Forever can reach the user's projects and samples safely.

## 1. The OS's `/fat32`
- **Medium: the same eMMC as `/yaffs2`, user area, no partition table.** One RSI (SD/MMC) driver (`rsi.c`) serves both; `fat_diskio.cpp` reads/writes through the same sector calls as `yaffs_te_drv.c` (`8c0c12b8`/`8c0c1224`) and never selects a hardware partition [L]. eMMC user area `EMMC_SECTOR_COUNT` = `0x1cf8000` sectors (15.55 GB; asserted in `8d4f9ed4`, `8d4fe3f4`) [L].
- **Layout (512-byte sectors) [L]:** yaffs = sectors `0..0x47ffff` (2.25 GiB: param from `8d4fe730("/yaffs2", 0, 0x11ff)`, 4 KiB chunks, 128 per block; driver `11a02404`/`11a024dc`: sector = chunk<<3, no base). FAT = logical `0..0xffffff` (8 GiB; ioctl `8d4f9bbc`: count `0x1000000`, size `0x200`), mapped by `8d4f9b40`: logical < `0xcf8000` -> physical + `0x1000000`, else unchanged. So the FAT fills physical `0xcf8000..0x1cf7fff`, boot sector at physical `0x1000000`. `0x480000..0xcf7fff` (4.2 GiB) is used by neither [I].
- **Mount and check [L]:** every OS start, on the storage processor only (`8c0c2218`, flag `B[0x86c05ca4]`==0, the same flag `yaffs_te_drv` asserts in both images, so processor A [I]): register `/yaffs2` and `/fat32` (`8c0bdb54` [I]), mount yaffs (`8d4fe804`), then `8c0c0aa4` (`samples_fat32.cpp`): FatFs `f_mount` (`8d4fe0dc`, drive "", immediate). **On failure it logs "mount failed (%i), format and retry..." and formats without asking** (`8d4fe00c`: `f_mkfs` FAT32, 2 FATs, params `0x840410ec`; mount; label "XY" [I]). No consistency check.
- **1.2.5:** same constants (`0xcf8000` `8c0f7c28`, `0x1cf8000` `8c0f7f78`, `0x11ff` `8c0fc0a6`) [L]. **1.1.33:** same `fat_diskio`/format strings [M]; constants not checked [U].
- Agrees with an earlier read: `target/fat_storage.cpp` (same mapping) and a read-only `native/fat32.cpp` (433 lines, host tests only) existed in the repository's history, since removed [F git]. The Test 017 census is a memory census, no mount list [F].

## 2. What each USB mode shows (from the code)
- **OS MTP:** one storage rooted at `/fat32/` (`0x840ae0e8`, used by `8d5c1ea2`, `8d5c2d5c`), filtered by regexes `0x840ae0c4..`: `/projects` (`.xy`, folders, `backups/NNNN.xy`), `/samples` (`.wav .aif .aiff .aifc`, two folder levels), `/presets` (`.preset`, incl. `snapshot/`), `/how_to_import.txt` (copied from `content/readme` at MTP start, `8d5c0e12`) [L]. Not shown: `/fat32/autosave`, other file types, all of `/yaffs2` (`user/op1.json`, patterns, tunings) [L]. The "secret" greeting flag `0x840068ac` is read only for the USB descriptor (`8c068060`) [L], so same roots [I].
- **Dev-menu MTP (1.2.1 bootloader):** one storage `/yaffs2` (009) [L]: `content/`, `user/`, `xy-forever/`, ...; no projects or samples. Its "OP-XY Disk" (update mode, `mass_storage.cpp`) is the FatFs RAM disk (`11a0b346`, extents `0x11900518`), not the user volume [L].

## 3. What the 1.2.1 bootloader can reach [L]
- **No FAT on eMMC:** no `fat_diskio`, `samples_fat32` or `0xcf8000`; its FatFs (ff13c) reads only the RAM disk, wired by direct calls, so it cannot be pointed at the eMMC without patching stock code (not allowed).
- **Raw eMMC sectors:** A-local reads `88a08574`->`88a07850` (n sectors) and `88a08558`->`88a077d0` (one), on the selected hardware partition; wrappers `889f6abe`/`889f6a6a` run them on A or send B's ops `0x12`/`0x11`. The yaffs media read (`88a345f4`) uses `889f6abe` with absolute user-area sectors, so the FAT sectors are readable the same way. Select `88a08710` writes EXT_CSD 179 (0 user, 1/2 boot, 4-7 GP).
- **Factory reset** (dev menu, `88a1f050`): deletes `/yaffs2/user`, `/yaffs2/logs`, `/yaffs2/content/dlc`, then `889f94f0` erases eMMC block `0x4000` = physical `0x1000000..`, the FAT's boot sector; the OS then reformats `/fat32` (section 1). "Format drive" (`88a1d73e`) erases yaffs blocks `0..0x11ff` only.

## 4. Options for XY Forever
| Option | Takes | Risk |
|---|---|---|
| (a) stock drivers only | nothing new, but stock offers raw sectors only: no FAT | cannot list or read files: not viable |
| (b) own FAT over stock raw read | A calls `88a08574` (one new allowlist entry; read only; no select: yaffs already needs the user area [I]); mapping (~60 lines) + read-only FAT32 reader (~450; both in that removed code) + engine glue: `/fat32` as a second read-only root (list, stat, read, hash) and a second MTP storage | read-only: none to data; a hung read = guard reset [U: `88a07850` wait bound] |
| (b+) writes too | own FAT writer (allocation, both FATs, FSInfo, directory entries, long names; ~1,500 lines [I]) + stock sector write | a bug or power cut loses the user's work; a bad boot sector makes the OS format the whole volume |
| (c) none before the OS | nothing | users go through the OS for everything |

**Recommendation: (b), read-only.** It gives what users ask for most (see and back up projects and samples) with one read primitive the yaffs path already relies on, and nothing that can damage the volume. Writes stay with the OS's own MTP; (b+) needs the developer's explicit yes, and only after (b) runs on the device.
**Meanwhile:** boot the OS from the library and use its MTP for projects, samples, presets. The OS mounts `/fat32` itself on every start (`8c0c2218` [L]; it uses no bootloader state [I]), so this works whoever loaded it [I; not yet observed after an XY Forever boot]. XY Forever's and the dev menu's MTP keep `/yaffs2` (settings, patterns, tunings). Autosave is reachable by neither.

## 5. Safety
- **Destroys data:** any write to the FAT's first sectors (boot sector, backup, FSInfo: physical `0x1000000..`) -> automatic format of 8 GiB on next OS start; writes to FAT tables or folders -> lost or cross-linked files (no checker anywhere); erasing block `0x4000` (= factory reset); a wrong mapping writing into yaffs (`< 0x480000`). **Can brick:** a partition select left on a boot partition makes later "user" I/O hit the boot partition.
- **Design rules for (b):** link no write, erase or select entry into the FAT path (builder audit); clamp every physical range to `0xcf8000..0x1cf7fff`; expose `/fat32` only after the boot sector checks out (`55 AA`, "FAT32", total <= `0x1000000`), else show it as unreadable: never repair, never format; guard armed around reads; the engine's path policy keeps refusing writes under `/fat32`.

## 6. Unknown, and what settles it
- **The device's FAT itself** (formatted, cluster size, free space): read physical sector `0x1000000` once (expect `55 AA` at 0x1fe, "FAT32   " at 0x52, total sectors at 0x20). No read-only command can do this today (raw layer refused): first step of (b). Sizes without code: saved stock OS logs give `/fat32` and yaffs size (lines `free user data`, `yaffs used`).
- **OS MTP after an XY Forever boot:** boot `opxy-121` from the library, list storages and folders (libmtp `mtp-detect`, `mtp-folders`): expect one ~8 GiB storage with `projects`, `samples`, `presets`, `how_to_import.txt`. Costs a user power cycle (OS is one-way).
- Whether `88a07850` waits forever on a failed transfer; whether the OS ever enables its mass-storage class ("OP-XY Disk", "MassStorage" strings); 1.1.33's constants.

**Open questions:** (1) read-only access to projects and samples from XY Forever (b): yes? (2) writes left to the OS's own MTP for now?
