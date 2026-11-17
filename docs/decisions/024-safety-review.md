# 024: Safety review of the file commands, mod job, commit, content switch, config and knobs (2.3)

Review record, 2026-11-15; code read only, no device, nothing built. Severity: **blocker** (can brick or break a binding rule), **should fix**, **note**. Each
finding names a reproduction idea.

## (A) Writes outside the write area

No path reaches the boot partition (raw layer refused on A first, `file_engine.cpp` `decide_request`, and while a boot partition is selected) or removes
`/yaffs2/bootloader.dev` (A: `decide_engine` plus the stat check around unlink/rmdir/rename, `storage_server.cpp:600-611`, `rename_now`). `..`, `.`, empty
components (doubled or trailing `/`), control bytes, a missing NUL within 240 bytes and a name holding `bootloader` are refused by `valid_path`/`write_root`
(`storage_server.cpp:22-58`) and by `xyf::content::well_formed`. Package text: mod ids pass `valid_id`, generation and profile names `job_token` (first byte
alnum, so never `.`/`..`; `usb_service.cpp:1176-1185`, `1842`); every job path is checked by `job_read_ok`/`job_write_ok` (`:1166-1177`).

1. **should fix — the write area is B-only for open-for-write, mkdir, unlink and rmdir.** A re-checks `write_root` only for rename (`storage_server.cpp:604`,
   `rename_now`). For the other changes A applies only `decide_engine`, which allows all of `/yaffs2/` (`user/` included) except removing the marker
   (`file_engine.cpp:73-82`; `serve_engine` runs the stock executor at `:606`). A B bug (a job path, a sweep name) would reach `user/`. Repro: host bridge, send
   op 0x26 `/yaffs2/user/x` through `file_client::mkdir` with the B check bypassed; A executes it. Fix: in `serve_engine` under `XYF_USB_FILE_WRITE` (not MTP
   mode), refuse `run_path` 0x25/0x26/0x27 and `run_open` with flags unless `write_root(path_copy)`.
2. **note — `content/` is writable by the file commands as a whole**, `content/dlc/` and official files included (`write_root`, `storage_server.cpp:47-49`):
   that is SAFETY.md's rule, not a defect. Only the content switch refuses `dlc/` and official paths (`xyf_content.hpp:121-133`). "Save `op1_factory.json`
   first" is a host rule; the firmware does not enforce it for `file.write`.
3. **note — the current default can be deleted or renamed by `file.delete`/`file.rename`** (anything under `xy-forever/`). Recoverable: XY Forever is in the
   boot partition and the menu stays (Test 059).
4. **should fix — the menu's Set default runs while a job owns the write window** (see 7): it can make `mods-a`/`mods-b` the default after `job_target` chose
   it, and the commit then renames the default into `tmp/job-<id>-old` and removes it (`usb_service.cpp:1418-1440`). Recoverable (menu), but breaks decision
   019's "never the default".
5. **note — links.** yaffs follows symlinks/hardlinks inside a path; only the marker is stat-guarded. No feature creates links, but `job_sweep`/`tree_step`
   descend into whatever `file.list` reports as a folder; a link planted under `tmp/job-*` by another tool would let the sweep unlink outside `tmp/`. Repro:
   sim, a symlink `tmp/job-1/l -> /yaffs2/user`, start a job.

## (B) Bounded waits and USB service

Every B request is bounded (`file_client.cpp:6-10`, 5 s / 45 s, `await_answer`); the end-of-job close is asynchronous (`settle_begin`/`settle_turn`); the
commit, tree removal and leftover sweep take one request per pass (`commit_step`, `tree_step`, `sweep_step`, caps 600). Knob reading only reads
(`knob_input.cpp:37-62`; at most 43 bytes of a 253-byte slot, 16-bit changes bounded).

6. **should fix — `job_sweep` runs in one loop pass** (`usb_service.cpp:1277-1305`): up to 1024 iterations of list + unlink/rmdir without answering USB, called
   from `job_finish` (`:1480`) on every failed or cancelled job. A cancelled job with a few hundred staged files (mod_job removes its files; folders and
   leftovers come here) blocks HELLO for seconds; worst case 1024 x (5 s + 45 s). Repro: sim, a job with a deep folder tree, `job.cancel`, then time HELLO. Fix:
   run it as `tree_step` stages (`js_clean`-like) from `job_pump`.

## (C) Power cut / failure

No path writes the boot partition or the marker, so a plain power-on always reaches XY Forever's menu, which boots any library entry. Commit order
(`commit_step`) never touches the default or boot-once entry; a cut after `js_away` leaves the old entry in `tmp/` (swept next job) and the target missing:
listed as missing, default unaffected. `config.txt` uses `.part`/`.old` (`xyf_config.hpp:23-25`) and the content record `.part`/`.old`
(`xyf_content.hpp:18-19`); a cut mid content switch leaves `content-state.txt` = switching, so the OS's content may be mixed but the menu returns. **note:**
`job_target` skips a state file that is `s_too_large` (`usb_service.cpp:1393`); A's scan reads the same bound, so both agree, but a corrupt newest slot can make
an older default win on both sides.

## (D) Untrusted input

`config.txt`: every field copied with its capacity (`xyf_config.hpp:52-55`), the stamp only 64 hex, slot only `mods-a`/`mods-b`. `content-state.txt`: entry at
most 31 id bytes, LF required (`xyf_content.hpp:40-47`). `files.sha256`/`content.sha256`: 64 hex, path <= 160 printable bytes (`:141-149`). Listing names:
length byte checked against 240 before the copy (`job_sweep`, `tree_step`, `sweep_step`). No overflow found. **note:** `job_ids_split` reports `s_bad_request`
for 33+ ids instead of `too many`; harmless.

## (E) Single-job rule

`job_blocks` (`usb_service.cpp:1133-1140`) covers every USB storage, install, boot and reboot command.

7. **should fix — the menu's own actions are not blocked by a running job** (`key_ui.cpp:535-552`): Continue, Discard, Boot once and Set default act at once;
   only Set default on a content-missing entry and Save see `s_busy`. Set default sends A a state write between the job's requests while its window is open
   (`file_boot.cpp:1089`, no `settle_writes` first, unlike USB `set_default`); Continue/Boot once abandon the job (A closes the window on the boot, so yaffs is
   safe; a content switch is left half done). `t_key` is not in `job_blocks`, so injected keys reach these too. Repro: sim, `mods.apply`, then `key` SHIFT+PLAY
   on an installed entry. Fix: `if (usb_service::page_busy()) { status = "Busy: a job is running."; continue; }` before those actions.

No blocker found.
