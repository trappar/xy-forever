# 006: Content and content packs (detail)

Detail of [006-content.md](006-content.md).

## 3. Stock content install (1.2.1 bootloader) [L unless marked]
- `.tfw` verified, decrypted, unzipped to `/yaffs2/upgrade/` (`88a27d00`); orchestrator `88a2863c` runs content (`88a28036`, state 0) before the OS (state 1).
  `88a2901a` sets content-only `B[0x88a291c4]` when no OS/companion/bootloader member exists and reads the pack `name` from
  `upgrade/.../content/dlc/*/manifest.json` (`88a2655c`).
- `88a28036`: pass 1 looks for a member named `partial_upgrade` (`88a27f08`, ROM `strstr`): PARTIAL or FULL. **FULL, not resuming:** rename `content/dlc` ->
  `upgrade/dlc_preserve`, delete `content/` (`11a01a32`: tree walk + remove), recreate, rename the packs back. **PARTIAL + content-only:** delete
  `content/dlc/<name>` for each pack folder in the package. Pass 2 copies every member but the marker to `content/<rel>` (overwrite), skipping the rest after a
  failed copy; unless content-only, then copies `firmware.version`.
- Never touched: `/yaffs2/user/`, `/fat32/`, `bootloader.dev`, `xy-forever/`. FULL wipes anything else in `content/` (open-xy files, user edits).
- Power cut: resume (SPI marker `0xab0d`, entry `88a28ff4` sets `B[0x88a291c0]`) skips the wipe and copies again; packs caught in `upgrade/dlc_preserve` are not
  moved back [I]; a cut mid-copy leaves a mixed tree until resumed.
- 1.2.5 bootloader: same mechanism strings (`partial_upgrade`, `upgrade/dlc_preserve`, "removing old DLC folder"), new wording ("installing content package";
  factory reset "removes all user data and content packs") [M: strings of its `bootloader.ldr`].
- **XY Forever, own code only:** 003's reader decrypts and inflates every `firmware/blackfin/content/` member (native `admit` accepts all three ZIPs [F: 003]);
  the M2 engine writes; `native/content` plans keep/install/erase per file against a verified previous manifest, rechecks before writing, resumes, and aborts on
  unknown modified files [L]. Missing: streaming ~126 MB (OS-only reads ~1.4 MB), write speed and free space [U], an installed-generation record, a policy for
  unknown files.

## 5. Options for a multi-entry library (facts for content per entry)
| Option | Takes | Risks |
|---|---|---|
| (a) one shared tree | nothing per switch; newest official content + every mod's additions | 1.1.33 broken as seen; entries for machines an OS lacks [U] (005 ask 4); one `op1_factory.json` must carry every mod set |
| (b) per entry, switched on default change | each entry keeps only its delta from a base generation in `firmware/<id>/content/` (1.1.33 vs 1.2.1: 15 files, ~100 KB; open-xy: catalog, svgs, presets); `native/content` apply at set-default (boot-once: switch twice, or boot mismatched); active-generation record; `firmware.version` rewritten | a cut mid-switch leaves mixed content until redone (recoverable); edited factory files abort the plan; the stock updater (recovery step 3) wipes `content/` but not the stored deltas |
| (c) leave alone, warn | compare the entry's expected manifest or generation id with `content/`, mark it in the menu | nothing written; 1.1.33 and mismatched mod menus stay broken |

## 7. Proposed split
| Step | Done when |
|---|---|
| build: manifests + status (host) | manifests (path, size, SHA-256) of the 1.1.33/1.2.1/1.2.5 content from the decrypted ZIPs as private fixtures plus a compact list the device can hold; `xyf_host.py content-status` names the matching version and lists extra/missing/modified files; simulator test |
| #17 device safe: content inventory | read-only: the device's `content/` (version, open-xy leftovers, packs) and yaffs free space recorded in STATUS; host backup of `content/` in the private folder |
| #18 build: content install job | all content members streamed through 003's reader into `native/content`: FULL = install/erase against the known previous generation, `content/dlc/` kept; PARTIAL = pack folder replaced, marker skipped; `firmware.version` last; generation record. Simulator: 1.2.1 -> 1.1.33 -> 1.2.1 byte-identical; a cut at every write resumes; `user/`, `content/dlc/`, `bootloader.dev` untouched; paths outside `content/` refused |
| #19 device risky: 1.1.33 round trip | after #17's backup: 1.1.33 content installed, `opxy-1133` booted (user: works?), 1.2.1 content restored, status matches 1.2.1 |
| #20 build: content packs | pack list, install of a content-only `.tfw` (synthetic, test key, host-only) and uninstall, from the menu and serial; simulator |
| #20 device: a real pack | blocked: needs a pack file |

**Open questions:** (1) may an install overwrite or delete `content/` files that match no official version, or must it stop or move them aside? (2) section 5:
(a), (b) or (c)? (3) back up `content/` (126 MB over USB) before the first write? (4) is a pack file (e.g. `dc_nyc`) available, and what said packs are new in
1.2.5? (5) may XY Forever install unsigned packs (plain folders)?
