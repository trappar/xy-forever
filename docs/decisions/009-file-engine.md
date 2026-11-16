# 009: File engine (plan item 2.1)

Research record, 2026-11-14; no device, no build. **[C]** read from our code/docs, **[L]** from the 1.2.1 listing (`re/bootloader.py`), **[I]** inferred, **[U]** unverified. **Question:** the engine's API, session, transport, streaming, path policy, concurrency, timeouts, tests and the split of 2.2.

## Findings that change the picture
- **Rename exists on A** [L]: the yaffs provider vtable `0x11906448` slot 7 (`889f165a`, so far read as "fstat") calls `889fa834(old,new)` -> `889fa604` = yaffsfs rename (both paths checked, both parent dirs found, EROFS/EXDEV/ENOTDIR/EISDIR/EINVAL/ENOENT codes) -> `88a01afa` = `yaffs_rename_obj` (finds the existing target, calls `yaffs_change_obj_name` twice: the shadowing replace). No link op carries it (no two-path request), so only A can call it; it lies inside the CRC-checked MTP spans [C]. ARCHITECTURE/STORAGE "rename not reachable" holds for the link only (fix after the device proves it). Without rename, MTP rename/move would be refused (clients copy + delete) and installs would rely on 003's `info.txt`-first order, with partial files left at their targets.
- **/fat32 is not the user's volume here** [L]: stock op-8 mount `889f75dc` (role 0) registers `/yaffs2` and `/fat32` (FatFs ff13c, vtable `0x11906004`), whose disk read `11a0b346` copies from RAM extents (`0x11900518..`): the update-mode RAM disk. XY Forever never calls that mount [C]. The OS's FAT medium is unknown (006) [U]. The engine exposes `/yaffs2` only.
- **Stock dev-menu MTP = the reference** [C `decide_mtp`, L]: one storage at `/yaffs2`; list, stat, space, open (any flags), read, write, lseek, close, truncate, unlink, mkdir, rmdir, access, set times; no rename/move/copy (no link op; the handler's immediates show no 0x1019/0x101a/0x9804, switch tables not decoded).
- **B's stock client** [L]: each of the 12 calls = builder + `889f869e` -> `889f7b12` (clear `req+4`, submit `11a0060c`, spin on `req+4`, return `req+0x10`). The builders do not wait: path `889f8d22`, read `889f6c5c`, write `889f8c10`, query `889f8e36`, readdir `889f88ac`, partition `889f87d4` (already allowlisted). Destructors only reset the vtable (`889f51ae`).

## Decisions
1. **Keep the proven A side:** B's file requests keep the stock wire shapes and A runs them with the stock executors (`889f76ca 889f6fea 889f6cd6 889f6dca 889f72e4 889f6b7c`) behind one new pure `decide_engine`. Why: 138 ran every file op through exactly this path; own executors would need unproven body layouts.
2. **B's own timed client** (~200 lines, 007): call the builder, submit through `Stock121PeerSession`'s single slot, poll `req+4` with a bound. Same bytes on the wire [I]; no `889f869e` on B. Why: removes 12 of 007's 18 crossings.
3. **One engine mode on A** (new `engine_item`, an `ask()` request: never lost [C]) replaces the read, file-write and MTP modes. Why: one policy, one window, fewer switches.
4. **A-local work uses direct yaffs calls** (proven: open/read/write/close/opendir/readdir/closedir) plus **one new call, `889fa834` rename**. Why: hashing and commit need no link round trips.
5. **Writes go to `/yaffs2/xy-forever/tmp/`, then rename** onto the target after A verifies size + CRC-32 (+ SHA-256). Write opens are admitted only under `tmp/`. Why: a cut leaves only `tmp/` debris; the target is old or new (yaffs shadowing [I]).
6. **Window = lazy, short, owned by A:** the first mutating request opens it (`open_write_window`, as 049/139); it closes on op 9, 10 s idle, any non-engine item (catalog, check, boot, set-default, install, reboot), or B gone. Close = close tracked handles, RW unmount (checkpoint), gates, `reopen_read_only`, rescan + catalog if anything changed; op 9's reply says "library changed" so B re-fetches the catalog. Why: no fresh boot; never RW while freezing or after B left.
7. **Policy on A, mirrored on B:** the same pure function in both. Why: A is authoritative; B's copy keeps brick paths away from a restarted stock A.

## Operations (B builder -> A op -> A executor; status)
| Operation | Path | Status |
|---|---|---|
| list (page = skip n, return k; size/type by stat) | `8d22` opendir 0x2e, `88ac` readdir 0x2f, `87d4` closedir 0x30 | A proven (097, 138); B builders new |
| stat / free space | `8e36` query 0x22 (0x34-byte record) / 0x23 (12 bytes) | A proven (097) |
| read range | `8d22` open 0x29 flags 0, `87d4` lseek 0x2c, `6c5c` read 0x2a <= 1 KiB, `87d4` close 0x2d | A proven (038/041, 138) |
| write stream | `8d22` open 0x29 flags `0x8301` in `tmp/`, `8c10` write 0x2b <= 1 KiB, close | A proven (049/050, 138); tmp rule new |
| commit, rename, move | tagged pair: path 0x21 with argument tags `RNFR`/`RNTO` -> A `889fa834` | new, unproven |
| hash job (CRC-32 + SHA-256) | tagged start; status = query-class reply body {state, done, total, crc, sha} | job new; reads proven |
| delete / mkdir / rmdir | `8d22` 0x25 / 0x26 / 0x27 (rmdir only empty; tree delete = caller loop) | A proven (050, 138) |
| mount (read-only) / sync = close window | `87d4` op 8 / op 9 (the window itself opens lazily, decision 6) | proven (080/081, 139) |

Not exposed: truncate 0x36, set times 0x35, fsync 0x34/0x28 (durable = file closed; checkpoint = window closed), every raw partition op. Errors: 004's statuses plus the yaffs errno in the reply. Tag encoding: fixed in 2.2a from the builders' argument layout.

## Path policy (A authoritative, B mirror)
- **Normalise:** absolute; `/yaffs2` then `/` or end; collapse `/` runs; drop a trailing `/`; refuse `.`/`..`, empty names, bytes < 0x20; <= 239 bytes + NUL (stock path buffer); names <= 255.
- **Refuse:** anything outside `/yaffs2` (incl. `/fat32`, relative); unlink, rmdir or rename-as-source of `/yaffs2` or `/yaffs2/bootloader.dev` (ASCII case ignored); write-opens outside `tmp/`; partition 4/0x14/0x18, sector 0x11/0x12, write 0x15/0x16 (boot partition: INSTALL only). SPI flash, metadata, OTP: no code path (builder's forbidden targets).
- **Allow everything else**, incl. overwriting or re-creating the marker (rename onto it) and deleting XY Forever's own files. Effect: a missing default or state slot opens the menu with the reason; stage 1 in the boot partition still gives menu + USB; reinstall over USB or the dev-menu kit. Missing `content/`: the OS says "Content not found" (reinstall); missing `user/`: OS factory init. All recoverable without hardware.

## Transport, timeouts, faults
- **One request outstanding** on B (session slot; keepalives and `ask` wait), submitted only from B's main loop: a request and its body are never split (146). Engine never uses op-10 commands (lossy). Each door session starts with `ask(engine_item)`: our A answers `~value`; a restarted stock A would not [I], so B stops.
- **Chunks:** 1 KiB both ways (proven); larger only after 2.5 measures. **Bounds on B:** 5 s per request, 45 s for a mutating request (RW mount took up to 16.85 s) and for op 9 (checkpoint write [U]). Past the bound the caller gets `link` (retryable: writes carry offsets); the slot stays in flight and new calls get `busy`; at the hard limit (> A's long guard count) the engine is "link lost" until reboot (menu says so, USB still answers).
- **Guard on throughout:** B's wait loop kicks its guard (no `guard_pause`). A arms its own guard while the window is open, reloads it between calls, uses INSTALL's long count around RW mount/unmount, disarms at close. A yaffs media retry forever (007 #4) ends in A's reset; yaffs then recovers like a power cut.
- **B gone:** A closes window + handles on B's bootloader requests (raw ops, as `run_mtp`), on 10 s idle, and before the guarded `a_alone_ms` reset (today it resets without closing). **Cable pulled / power cut:** B's job drops after 10 s without host data; partial bytes stay in `tmp/`, which B's client sweeps after the next window open. A cut while RW: no checkpoint, so one slow boot (full scan 4.5-17 s [C]).
- **Concurrency:** B is one loop; serial and MTP never run together (one USB driver slot, 002). One job owns the engine (004); others get `busy` for mutations. A tracks <= 4 files + 4 folders; the window opens only with none open (else `busy`). **Memory, speed:** B holds one 1 KiB buffer (no staging; 7 MB staging stays for INSTALL); A uses the stock bodies + a <= 64 KiB job buffer. Measured: NAND read 4.9 MB/s; a 2.37 MB boot image crosses in BOOT -> hand-off 2.7 s (084), so multi-KiB 0x12 bodies are ~1 MB/s or more [I]. Not measured: 1 KiB request round trip, NAND write, CDC stop-and-wait. 006's 126 MB content install depends on them.

## Unproven, rejected
- **Unproven (device only):** B builders + bounded poll; yaffs rename on the device and its power-safe replace (yaffs2 design [I]); window open/close times and the next boot's mount after our close; chunks > 1 KiB; throughput; A's long guard count in seconds; stock-A detection. Simulator (1.3) proves policy, client state machine, window lifecycle, tmp/rename/sweep and the faults in 2.2b-d against a fake link and a folder; it cannot prove stock code.
- **Rejected:** Own executors or body parsing on A (unproven layouts). Own link or wire protocol (007: 600-1000 lines). Writes in place (partial targets). One window per file (a checkpoint each). A window held open while idle (RW after B dies). Whole-file RAM staging. `/fat32` through the stock FatFs (RAM disk). Pausing the guard.

## Split of 2.2 (each after 1.3; order a, b, c, d)
| Item | Done when |
|---|---|
| 2.2a build: policy + engine mode (A) | `decide_engine` replaces the file allowlists of `decide`/`decide_write`/`decide_mtp`; normalisation, refusals, tmp-only write opens, tag parsing; same function on B; host tests per rule |
| 2.2b build: B's timed client | 5 builders on the allowlist; submit via the slot; soft/hard bounds, `busy`, link lost, `engine_item` check; READ_FILE, FILE_WRITE, mtp-test moved onto it; `CLIENT_CALLS`, `889f28b8`, `889f3320`, `MTP_TEST_CALLS` gone from B; no `guard_pause`; simulator: A silent, slow, late reply, link error mid-body; product builds and verifies |
| 2.2c build: window lifecycle (A) | lazy open, every close trigger, long guard count, rescan + changed flag, close before `a_alone_ms` reset; simulator: write then boot in one boot, B gone mid-window |
| 2.2d build: streaming + commit | write begin/data/end via `tmp/`, hash job, rename commit (`889fa834`), sweep, list paging, ranged reads, free-space check; simulator: cable pulled mid-write, storage full, power cut between every two steps |

**2.3 checks:** every brick path refused on A and on B, with tests; no role-1 path reaches `889f869e`/`889f7b12` (posix close/closedir stay allowlisted for A only); every wait bounded and the guard never paused; window closed before any freeze, boot, set-default or reset; the six new entries do what is claimed and reach no forbidden target.

**Open questions:** (1) the OS's `/fat32` (projects, samples) is out of reach with stock code: is `/yaffs2` only acceptable for now? (2) allow six new stock entries: five request builders on B and yaffs rename on A (all inside existing CRC spans)?
