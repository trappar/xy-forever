# 009: File engine

Research record, 2026-11-14; no device, no build. **[C]** read from our code/docs, **[L]** from the 1.2.1 listing (`re/bootloader.py`), **[I]** inferred,
**[U]** unverified. **Question:** the engine's API, session, transport, streaming, path policy, concurrency, timeouts, tests and the build steps.

## Findings that change the picture
- **Rename exists on A** [L]: the yaffs provider vtable `0x11906448` slot 7 (`889f165a`, so far read as "fstat") calls `889fa834(old,new)` -> `889fa604` =
  yaffsfs rename (both paths checked, both parent dirs found, EROFS/EXDEV/ENOTDIR/EISDIR/EINVAL/ENOENT codes) -> `88a01afa` = `yaffs_rename_obj` (finds the
  existing target, calls `yaffs_change_obj_name` twice: the shadowing replace). No link op carries it (no two-path request), so only A can call it; it lies
  inside the CRC-checked MTP spans [C]. ARCHITECTURE/STORAGE "rename not reachable" holds for the link only (fix after the device proves it). Without rename,
  MTP rename/move would be refused (clients copy + delete) and installs would rely on 003's `info.txt`-first order, with partial files left at their targets.
- **/fat32 is not the user's volume here** [L]: stock op-8 mount `889f75dc` (role 0) registers `/yaffs2` and `/fat32` (FatFs ff13c, vtable `0x11906004`), whose
  disk read `11a0b346` copies from RAM extents (`0x11900518..`): the update-mode RAM disk. XY Forever never calls that mount [C]. The OS's FAT medium is unknown
  (006) [U]. The engine exposes `/yaffs2` only.
- **Stock dev-menu MTP = the reference** [C `decide_mtp`, L]: one storage at `/yaffs2`; list, stat, space, open (any flags), read, write, lseek, close,
  truncate, unlink, mkdir, rmdir, access, set times; no rename/move/copy (no link op; the handler's immediates show no 0x1019/0x101a/0x9804, switch tables not
  decoded).
- **B's stock client** [L]: each of the 12 calls = builder + `889f869e` -> `889f7b12` (clear `req+4`, submit `11a0060c`, spin on `req+4`, return `req+0x10`).
  The builders do not wait: path `889f8d22`, read `889f6c5c`, write `889f8c10`, query `889f8e36`, readdir `889f88ac`, partition `889f87d4` (already
  allowlisted). Destructors only reset the vtable (`889f51ae`).

## Decisions
1. **Keep the proven A side:** B's file requests keep the stock wire shapes and A runs them with the stock executors
   (`889f76ca 889f6fea 889f6cd6 889f6dca 889f72e4 889f6b7c`) behind one new pure `decide_engine`. Why: 138 ran every file op through exactly this path; own
   executors would need unproven body layouts.
2. **B's own timed client** (~200 lines, 007): call the builder, submit through `Stock121PeerSession`'s single slot, poll `req+4` with a bound. Same bytes on
   the wire [I]; no `889f869e` on B. Why: removes 12 of 007's 18 crossings.
3. **One engine mode on A** (new `engine_item`, an `ask()` request: never lost [C]) replaces the read, file-write and MTP modes. Why: one policy, one window,
   fewer switches.
4. **A-local work uses direct yaffs calls** (proven: open/read/write/close/opendir/readdir/closedir) plus **one new call, `889fa834` rename**. Why: hashing and
   commit need no link round trips.
5. **Writes go to `/yaffs2/xy-forever/tmp/`, then rename** onto the target after A verifies size + CRC-32 (+ SHA-256). Write opens are admitted only under
   `tmp/`. Why: a cut leaves only `tmp/` debris; the target is old or new (yaffs shadowing [I]).
6. **Window = lazy, short, owned by A:** the first mutating request opens it (`open_write_window`, as 049/139); it closes on op 9, 10 s idle, any non-engine
   item (catalog, check, boot, set-default, install, reboot), or B gone. Close = close tracked handles, RW unmount (checkpoint), gates, `reopen_read_only`,
   rescan + catalog if anything changed; op 9's reply says "library changed" so B re-fetches the catalog. Why: no fresh boot; never RW while freezing or after B
   left.
7. **Policy on A, mirrored on B:** the same pure function in both. Why: A is authoritative; B's copy keeps brick paths away from a restarted stock A.

Detail: Operations, Path policy, Transport, Split in [009-file-engine.detail.md](009-file-engine.detail.md)

## Unproven, rejected
- **Unproven (device only):** B builders + bounded poll; yaffs rename on the device and its power-safe replace (yaffs2 design [I]); window open/close times and
  the next boot's mount after our close; chunks > 1 KiB; throughput; A's long guard count in seconds; stock-A detection. Simulator proves policy, client state
  machine, window lifecycle, tmp/rename/sweep and the faults of the steps below against a fake link and a folder; it cannot prove stock code.
- **Rejected:** Own executors or body parsing on A (unproven layouts). Own link or wire protocol (007: 600-1000 lines). Writes in place (partial targets). One
  window per file (a checkpoint each). A window held open while idle (RW after B dies). Whole-file RAM staging. `/fat32` through the stock FatFs (RAM disk).
  Pausing the guard.
