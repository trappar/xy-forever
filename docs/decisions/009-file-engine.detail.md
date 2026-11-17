# 009: File engine (detail)

Detail of [009-file-engine.md](009-file-engine.md).

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

Not exposed: truncate 0x36, set times 0x35, fsync 0x34/0x28 (durable = file closed; checkpoint = window closed), every raw partition op. Errors: 004's statuses
plus the yaffs errno in the reply. Tag encoding: fixed in the first build step from the builders' argument layout.

## Path policy (A authoritative, B mirror)
- **Normalise:** absolute; `/yaffs2` then `/` or end; collapse `/` runs; drop a trailing `/`; refuse `.`/`..`, empty names, bytes < 0x20; <= 239 bytes + NUL
  (stock path buffer); names <= 255.
- **Refuse:** anything outside `/yaffs2` (incl. `/fat32`, relative); unlink, rmdir or rename-as-source of `/yaffs2` or `/yaffs2/bootloader.dev` (ASCII case
  ignored); write-opens outside `tmp/`; partition 4/0x14/0x18, sector 0x11/0x12, write 0x15/0x16 (boot partition: INSTALL only). SPI flash, metadata, OTP: no
  code path (builder's forbidden targets).
- **Allow everything else**, incl. overwriting or re-creating the marker (rename onto it) and deleting XY Forever's own files. Effect: a missing default or
  state slot opens the menu with the reason; stage 1 in the boot partition still gives menu + USB; reinstall over USB or the dev-menu kit. Missing `content/`:
  the OS says "Content not found" (reinstall); missing `user/`: OS factory init. All recoverable without hardware.

## Transport, timeouts, faults
- **One request outstanding** on B (session slot; keepalives and `ask` wait), submitted only from B's main loop: a request and its body are never split (146).
  Engine never uses op-10 commands (lossy). Each door session starts with `ask(engine_item)`: our A answers `~value`; a restarted stock A would not [I], so B
  stops.
- **Chunks:** 1 KiB both ways (proven); larger only after #6 measures. **Bounds on B:** 5 s per request, 45 s for a mutating request (RW mount took up to 16.85
  s) and for op 9 (checkpoint write [U]). Past the bound the caller gets `link` (retryable: writes carry offsets); the slot stays in flight and new calls get
  `busy`; at the hard limit (> A's long guard count) the engine is "link lost" until reboot (menu says so, USB still answers).
- **Guard on throughout:** B's wait loop kicks its guard (no `guard_pause`). A arms its own guard while the window is open, reloads it between calls, uses
  INSTALL's long count around RW mount/unmount, disarms at close. A yaffs media retry forever (007 #4) ends in A's reset; yaffs then recovers like a power cut.
- **B gone:** A closes window + handles on B's bootloader requests (raw ops, as `run_mtp`), on 10 s idle, and before the guarded `a_alone_ms` reset (today it
  resets without closing). **Cable pulled / power cut:** B's job drops after 10 s without host data; partial bytes stay in `tmp/`, which B's client sweeps after
  the next window open. A cut while RW: no checkpoint, so one slow boot (full scan 4.5-17 s [C]).
- **Concurrency:** B is one loop; serial and MTP never run together (one USB driver slot, 002). One job owns the engine (004); others get `busy` for mutations.
  A tracks <= 4 files + 4 folders; the window opens only with none open (else `busy`). **Memory, speed:** B holds one 1 KiB buffer (no staging; 7 MB staging
  stays for INSTALL); A uses the stock bodies + a <= 64 KiB job buffer. Measured: NAND read 4.9 MB/s; a 2.37 MB boot image crosses in BOOT -> hand-off 2.7 s
  (084), so multi-KiB 0x12 bodies are ~1 MB/s or more [I]. Not measured: 1 KiB request round trip, NAND write, CDC stop-and-wait. 006's 126 MB content install
  depends on them.

## Split (each after the simulator; in this order)
| Step | Done when |
|---|---|
| build: policy + engine mode (A) | `decide_engine` replaces the file allowlists of `decide`/`decide_write`/`decide_mtp`; normalisation, refusals, tmp-only write opens, tag parsing; same function on B; host tests per rule |
| build: B's timed client | 5 builders on the allowlist; submit via the slot; soft/hard bounds, `busy`, link lost, `engine_item` check; READ_FILE, FILE_WRITE, mtp-test moved onto it; `CLIENT_CALLS`, `889f28b8`, `889f3320`, `MTP_TEST_CALLS` gone from B; no `guard_pause`; simulator: A silent, slow, late reply, link error mid-body; product builds and verifies |
| build: window lifecycle (A) | lazy open, every close trigger, long guard count, rescan + changed flag, close before `a_alone_ms` reset; simulator: write then boot in one boot, B gone mid-window |
| #4 build: streaming + commit | write begin/data/end via `tmp/`, hash job, rename commit (`889fa834`), sweep, list paging, ranged reads, free-space check; simulator: cable pulled mid-write, storage full, power cut between every two steps |

**#5 checks:** every brick path refused on A and on B, with tests; no role-1 path reaches `889f869e`/`889f7b12` (posix close/closedir stay allowlisted for A
only); every wait bounded and the guard never paused; window closed before any freeze, boot, set-default or reset; the six new entries do what is claimed and
reach no forbidden target.

**Open questions:** (1) the OS's `/fat32` (projects, samples) is out of reach with stock code: is `/yaffs2` only acceptable for now? (2) allow six new stock
entries: five request builders on B and yaffs rename on A (all inside existing CRC spans)?
