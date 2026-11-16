# 006: Content and content packs (plan item 4.5)

Research record, 2026-11-14; no device, no build. **[L]** read from a listing or our code (1.2.1 bootloader `bootloader-121-listing`; OS linear listings of 1.2.1 and 1.2.5, strings resolved from the `os.ldr` images), **[F]** read from a package, doc or saved device listing, **[M]** measured on files in the private folder, **[I]** inferred, **[U]** unknown. OS addresses are 1.2.1 unless noted. Listings and device evidence: the maintainer's (not shared).

**Question:** what content is, how it depends on the OS version, how stock installs it and content packs, what XY Forever can do with its own decoder, and the options for a library with several entries (input to 4.6 and 5.2d).

## 1. What content is
- **Package:** `firmware/blackfin/content/` = 839 files, 125,790,852 B in 1.2.1 and 1.2.5 (identical), 829 in 1.1.33 [M]. `samples` 469 files / 112 MB, `legacy_projects2` 8.1 MB, `display` 85 svg/otf 1.9 MB, `wavetables` 9 `.mip` 1.8 MB, `presets` 165, `projects` 13 `.xy`, `debug_projects`, `legacy_*`, `beats`, `patterns`, `tunings`, `readme`, `op1.json` 10,557 B, `op1_factory.json` 5,501 B [M]. No code [F: `analysis/tfw-install-001/OS125-SAFETY.md`].
- **On the device:** `/yaffs2/content/` (stock works relative to `/yaffs2`; `88a28036` copies to `content/%s`) [L]. The root also holds `firmware.version` (8 B), `user/`, `upgrade/`, `tmp/`, `logs/`, `bootloader.dev`, `xy-forever/`, and `layout.json`/`build.json` from an open-xy install [F: a device probe log]. The device's `content/` was never listed or hashed [U]; it may hold open-xy's `op1_factory.json` [I].
- **OS use (`db2` init `8d4e056c`; 1.2.5 `8c0df14c`, same strings) [L]:** makes `user`, `tmp`, `user/patterns`, `user/tunings`, `/fat32/{samples/user,presets/snapshot,projects/templates,autosave}`. No `content` folder: "Content not found. Please reinstall firmware", then `8fed2b24` (a halt [I]). No `user/init_complete`: "performing factory init" copies `content/op1.json` -> `user/op1.json` and `content/projects/basby.xy` -> `/fat32/projects/workspace.xy`, writes `user/init_complete`. Then loads `content/op1_factory.json` (filter/fx/lfo/seq/engine type catalog). Run time reads in place: `content/{samples,presets,patterns,beats,wavetables,tunings,projects/templates,display}` (`path_shortcuts.cpp`; the 9 wavetable names are fixed in code); "refusing to delete factory preset". The OS shows `/yaffs2/firmware.version`, not its own version (`8c0b84e8` [L]; Test 053 [F]).
- **Factory vs user:** factory = `content/` minus `content/dlc/` (packs). User = `/fat32/` (projects, `samples/user`, presets, snapshots, autosave: the "OP-XY Disk" of the readme) and `/yaffs2/user/` (`op1.json` settings database, patterns, tunings, beats) [L]. `/fat32`'s medium is not established [U].

## 2. Content and firmware version
- 1.2.1 -> 1.2.5: identical [M]. 1.1.33 -> 1.2.1: `op1_factory.json` gains filter `dynamic` (id 18) and engine `drumline`; `display/filter.svg`, `usb_audio.svg` change; `display/dbox.svg`, `dbox_t2.svg`, `presets/drumline/` (9 files) added; `display/usb_audio_routing_concept.svg` removed: 15 files, ~100 KB [M]. The 1.1.33 OS has no `drumline` string [M].
- No OS checks a content version, only that `content` exists and `user/init_complete` [L]. What an OS does with catalog entries it lacks (1.1.33 and `drumline`; any OS and absent open-xy machines) or missing svg/presets/wavetables is unknown [U].
- "Per-firmware content (1.1.33)" (HISTORY 055/056, GOALS, STATUS `opxy-1133`, an earlier USB study): 1.1.33 booted "pretty broken" on 1.2.1 content. Cause not isolated [I]: the newer catalog, or `user/op1.json` and `/fat32` projects last written by 1.2.1. The boot evidence needs a recheck (STATUS).

## 3. Stock content install (1.2.1 bootloader) [L unless marked]
- `.tfw` verified, decrypted, unzipped to `/yaffs2/upgrade/` (`88a27d00`); orchestrator `88a2863c` runs content (`88a28036`, state 0) before the OS (state 1). `88a2901a` sets content-only `B[0x88a291c4]` when no OS/companion/bootloader member exists and reads the pack `name` from `upgrade/.../content/dlc/*/manifest.json` (`88a2655c`).
- `88a28036`: pass 1 looks for a member named `partial_upgrade` (`88a27f08`, ROM `strstr`): PARTIAL or FULL. **FULL, not resuming:** rename `content/dlc` -> `upgrade/dlc_preserve`, delete `content/` (`11a01a32`: tree walk + remove), recreate, rename the packs back. **PARTIAL + content-only:** delete `content/dlc/<name>` for each pack folder in the package. Pass 2 copies every member but the marker to `content/<rel>` (overwrite), skipping the rest after a failed copy; unless content-only, then copies `firmware.version`.
- Never touched: `/yaffs2/user/`, `/fat32/`, `bootloader.dev`, `xy-forever/`. FULL wipes anything else in `content/` (open-xy files, user edits).
- Power cut: resume (SPI marker `0xab0d`, entry `88a28ff4` sets `B[0x88a291c0]`) skips the wipe and copies again; packs caught in `upgrade/dlc_preserve` are not moved back [I]; a cut mid-copy leaves a mixed tree until resumed.
- 1.2.5 bootloader: same mechanism strings (`partial_upgrade`, `upgrade/dlc_preserve`, "removing old DLC folder"), new wording ("installing content package"; factory reset "removes all user data and content packs") [M: strings of its `bootloader.ldr`].
- **XY Forever, own code only:** 003's reader decrypts and inflates every `firmware/blackfin/content/` member (native `admit` accepts all three ZIPs [F: 003]); the M2 engine writes; `native/content` plans keep/install/erase per file against a verified previous manifest, rechecks before writing, resumes, and aborts on unknown modified files [L]. Missing: streaming ~126 MB (OS-only reads ~1.4 MB), write speed and free space [U], an installed-generation record, a policy for unknown files.

## 4. Content packs
- **Not new in 1.2.5:** the 1.2.1 bootloader installs them and "2. uninstall content packs" (`88a1df4e`, `88a1e27e`) deletes every `/yaffs2/content/dlc/*` [L]; the 1.1.33, 1.2.1 and 1.2.5 OSes read `content/dlc/` [M]. 1.2.1/1.2.5 OS: each `content/dlc/<pack>/manifest.json` (`name`, `author`, `description`, `version`) is logged "dlc pack: %s name=%s version=%s" and registered as a path shortcut (reserved names refused); startup tests `content/dlc/dc_nyc/manifest.json` (`8c0b8698`, purpose unknown) [L]; pack `presets` and `projects` templates appear beside the factory ones [I from strings].
- **Format** [I from the bootloader code]: a TE-signed `.tfw` whose ZIP holds only `firmware/blackfin/content/dlc/<pack>/...` plus a `partial_upgrade` marker (without it the FULL path would wipe factory content). Delivered through the bootloader's update mode (USB disk or MIDI); the OS has no install path [F: OS125-SAFETY]; no host tool known.
- **Missing:** an actual pack file (none in the maintainer's evidence): container fields (key index, signature key, SKU, version), member layout and the marker are unverified; where TE publishes packs is unknown.
- **XY Forever on bootloader 1.2.1:** 4.3's decoder and signature check, then PARTIAL semantics (delete `content/dlc/<pack>/`, copy, skip the marker); uninstall = delete the folder. The OS reads plain folders, so a pack copied by MTP should register too [I, untested].

## 5. Options for a multi-entry library (facts for 5.2d)
| Option | Takes | Risks |
|---|---|---|
| (a) one shared tree | nothing per switch; newest official content + every mod's additions | 1.1.33 broken as seen; entries for machines an OS lacks [U] (005 ask 4); one `op1_factory.json` must carry every mod set |
| (b) per entry, switched on default change | each entry keeps only its delta from a base generation in `firmware/<id>/content/` (1.1.33 vs 1.2.1: 15 files, ~100 KB; open-xy: catalog, svgs, presets); `native/content` apply at set-default (boot-once: switch twice, or boot mismatched); active-generation record; `firmware.version` rewritten | a cut mid-switch leaves mixed content until redone (recoverable); edited factory files abort the plan; the stock updater (recovery step 3) wipes `content/` but not the stored deltas |
| (c) leave alone, warn | compare the entry's expected manifest or generation id with `content/`, mark it in the menu | nothing written; 1.1.33 and mismatched mod menus stay broken |

## 6. Safety
- User data (`/fat32/`, `/yaffs2/user/`) is outside every content operation; the path policy allows writes only under `content/` plus `firmware.version`. Never delete `user/init_complete` or `user/op1.json`: factory init would overwrite the user's settings database [L copy; I effect].
- Packs are signed files the user may not keep: a FULL-style wipe must preserve `content/dlc/` and a cut must not strand it [I].
- Missing or mixed `content/` breaks the OS, not XY Forever (it runs first and never reads `content/`): reinstall from a `.tfw` or by MTP, dev menu, or official 1.2.1 `.tfw` (ladder step 3). Recoverable without hardware, as is a full yaffs (delete over MTP) [I].
- No content operation touches the boot partition, SPI, OTP, companions or `/yaffs2/bootloader.dev` (root, outside `content/`), and none uses stock updater code.

## 7. Proposed split of 4.6
| Item | Done when |
|---|---|
| 4.6a build: manifests + status (host) | manifests (path, size, SHA-256) of the 1.1.33/1.2.1/1.2.5 content from the decrypted ZIPs as private fixtures plus a compact list the device can hold; `xyf_host.py content-status` names the matching version and lists extra/missing/modified files; simulator test |
| 4.6b device safe: content inventory | read-only: the device's `content/` (version, open-xy leftovers, packs) and yaffs free space recorded in STATUS; host backup of `content/` in the private folder |
| 4.6c build: content install job | all content members streamed through 003's reader into `native/content`: FULL = install/erase against the known previous generation, `content/dlc/` kept; PARTIAL = pack folder replaced, marker skipped; `firmware.version` last; generation record. Simulator: 1.2.1 -> 1.1.33 -> 1.2.1 byte-identical; a cut at every write resumes; `user/`, `content/dlc/`, `bootloader.dev` untouched; paths outside `content/` refused |
| 4.6d device risky: 1.1.33 round trip | after 4.6b's backup: 1.1.33 content installed, `opxy-1133` booted (user: works?), 1.2.1 content restored, status matches 1.2.1 |
| 4.6e build: content packs | pack list, install of a content-only `.tfw` (synthetic, test key, host-only) and uninstall, from the menu and serial; simulator |
| 4.6f device: a real pack | blocked: needs a pack file |

**Open questions:** (1) may an install overwrite or delete `content/` files that match no official version, or must it stop or move them aside? (2) 5.2d: (a), (b) or (c)? (3) back up `content/` (126 MB over USB) before the first write? (4) is a pack file (e.g. `dc_nyc`) available, and what said packs are new in 1.2.5? (5) may XY Forever install unsigned packs (plain folders)?
