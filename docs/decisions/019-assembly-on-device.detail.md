# 019: Where mod assembly runs on the device (detail)

Detail of [019-assembly-on-device.md](019-assembly-on-device.md).

## 1. Memory while XY Forever runs (same map on both processors, separate SDRAM [I])
| Range | What is there | Job may use |
|---|---|---|
| `0x88000000..0x88a63bf8` | bootloader image, driver data and BSS [C census] | never |
| `0x88a65bf8..0x8aa65bf4` | stock allocator arena, 32 MiB: yaffs device, caches; A's break `0x88b53398`, ~950 KiB used [M 069]; grows with files [U] | never (stock owns it; the peer session refuses it [C]) |
| `0x8a000000..0x8a800000` | boot source, inside the arena: the image is copied here at hand-off [C cold.cpp] | never |
| `0x8ab00000..0x8ab20000` | cold module: 90,440 B of 114,688 used, **24,248 B free** [M 158], 16 KiB stack | code only |
| `0x8ab20000..0x8ac00000` / `0x8ac00000..0x8b000000` | stage-2 window / nothing references it [C] | no / reserve |
| **`0x8b000000..0x8b800000`** | embedded OS source of offline modes; product has no third record since 088 [C]; outside the bootloader's static image and the arena (`check_boot_reservations`) [C]; stage 2 must not load here [C `xyf_stage2.hpp`] | **yes, A: the job region** |
| `0x8b800000..0x8b823000` | session scratch, catalog, library, scan, status, USB frames, file-client buffers [C] | never |
| `0x8b820000..0x8c000000` | staging: A's CHECK reads and set-default plan; B's USB staging 7 MiB + read-back; 003's `.tfw` decoder on B, 3,440,640 B (`tfw_os::scratch_bytes`); reboot marker `0x8bfff000` [C] | never |
| `0x8c000000..0x90000000` | B's framebuffer to `0x8c03c000`; stock OS loads `0x8c03d000..0x8d6036b0`, `0x8fed0000..` at hand-off [M records]; reporter `0x8d4ce380..0x8d4d0380` [C] | never |
| `0x80000000..0x88000000` | stock update mode uses `0x80004000+0x7f00000` as its RAM disk [L 003]; OS loads `0x84000000..0x86c0861c` [M]; XY Forever never touched it; cold-time mapping [U] | not needed |
- 008's "~6 MiB scratch region" had no address; it is real as above [C/I]. OS load regions are free during the menu (nothing persists into boot), but the job
  ends before any CHECK or BOOT, so it never shares staging or the boot source. The assembled image fits every boot buffer: staging 8,256,000 B, boot source 8
  MiB [C].

## 3. The job (A) and the shape rule
- **Flow** [I]: B writes `tmp/<job>/job.txt` (selection, target id, generation) and makes the folders through the engine, then starts job `assemble`. A, each
  file read whole with direct yaffs open/read/close (no lseek on A): base tables (edits.bin 8,888 B, objects.txt, absent.txt, …) → stock `os.ldr` into the image
  buffer, SHA-256 = `needs_os` → pass 1 per object (headers, symbols; numbering, placement) → pass 2 per object (bytes + relocations straight into the image
  buffer at `stock_bytes`, per-file SHA-256 against `files.sha256`) → edits, record, first header, restore check, `ldr::inspect` → stream-write `os.ldr`,
  `layout.json`, `content/op1_factory.json` (≤ 16 KiB [I]; official 5,501 B), svgs and presets copied, `files.sha256`, `info.txt` last → read back each file,
  SHA-256 + CRC-32.
- **Peak memory** [I]: A = image buffer 4,454,120 B + one object ≤ 1 MiB (largest `troupe/voice.o` 896,192 B [M]) + link tables ≤ 512 KiB + 64 KiB I/O ≈ 6.0 MiB
  of 8. B = nothing new (one status body).
- **Bounded** [I]: the job runs in slices from A's poll loop: one object step, one edit pass or one I/O chunk (start 16 KiB; set by the yaffs write pause, §8),
  so A serves B's requests between slices and reloads its guard; the long guard count stays only around window open and close, as today. B polls `job.status`
  (query class, 5 s bound) every ≤ 250 ms, which also keeps A's 25 s alone rule satisfied; its own guard kicks in that wait loop. The window stays open while
  the job runs (the job counts as an engine item).
- **Cancel, faults**: `job.cancel` sets a flag A checks each slice; A closes its two handles; B deletes `tmp/<job>/` (or the next sweep does). B silent 10 s: A
  aborts the same way. Link lost (60 s): B shows it; the debris is swept at the next start (010). Progress: {phase, done, total, status, detail} to B's screen
  and as 004's `job.status`/`progress`.

## 4. Time on A (link CPU < 0.1 s [I 008]; reads 4.9-17.7 MB/s, SHA-256 ~12 MB/s, CRC-32 13.6 MB/s [M q-085: B's SHA-256 198 ms, A's CRC-32 174 ms for 2.37 MB])
| Selection | objects read | module | `os.ldr` | content | reads + hashing | writes at 1 MB/s (0.5-4) | Apply |
|---|---|---|---|---|---|---|---|
| 1 machine (fm2op) | ~0.45 MB | 0.12 MB [M 008] | 2.49 MB | ~0.05 MB | 1.4-2.2 s | 2.6 s (0.6-5.1) | **~4.5 s** (2.5-8) |
| 7 typical (median 59 KB objects, ~50 KB content [M]) | ~0.8 MB | ~0.45 MB | ~2.85 MB | ~0.35 MB | 1.7-2.8 s | 3.2 s (0.8-6.4) | **~5.5 s** (3-10) |
| 16, largest that fits | ~2.2 MB | 1.75 MB | 4.13 MB | ~0.9 MB | 2.6-4.3 s | 5.0 s (1.3-10) | **~8.5 s** (4-15) |
- Plus window open/close: 0.31 s for a whole 2,500 B put-file with a checkpoint [M pass-4], up to 16.85 s after an invalidated checkpoint [M]. **Unmeasured:**
  yaffs write speed on A, the checkpoint write at close, the longest pause of one write call (garbage collection).

## 8. Split of #24; stock calls; open points
| Step | Done when |
|---|---|
| build: A job `assemble` | job region reserved (layout header + builder check); whole-file loads, slices, in-RAM checks, stream write, read-back, status, cancel, abort on B silent; simulator (host-compiled A): fm2op, a 7- and a 16-machine selection give `tmp/` bytes equal to `core.mjs`; high-water memory ≤ 8 MiB and the longest slice reported; product builds, size reported |
| build: B controller, commit, offer | preflight and target choice, `decide_apply` on both sides, folders, `job.status` polling with bounds, layout then rename commit (#11), CHECK, boot-once offer; `mods.apply`/`job.status`/`job.cancel` in `protocol/v1.json` + conformance cases; simulator: a cut between every two writes, `a_silent`, `cable_pull`, `space`, cancel in every phase leave default, other mods id and layout valid, sweep repairs |
| review | fresh agent: every wait bounded, guard reload intervals, write set, region disjoint from every reservation, checks complete against `core.mjs`, no new stock call |
- **Stock calls:** none new. A: direct yaffs open/read/write/close (proven, `cold_storage` spans); rename `889fa834` (allowlisted, through the engine as in #4);
  mkdir/unlink/rmdir only as B's engine requests to A's allowlisted executors. **Settles the time** (one safe measurement): in #6, put a 4 MiB file into `tmp/`
  with A timing its own write calls and the window close, then delete it; no new stock call, but it is a multi-MB write. **Open:** fit in stage 1 (if not: stage
  2 first); folder rename on the device [U 009]; boot once with mods content (010 question 1).
