# 012: Boot-partition writer on processor A (plan item 2.6)

Research record, 2026-11-14; no device, no build. **[C]** our code, **[L]** 1.2.1 listing (`re/bootloader.py`, `re/reach.py`), **[F]** earlier record or test, **[I]** inferred, **[U]** unverified. **Question:** can the stock writer `88a06a7e` run on A, where nothing waits on the link; is that as safe as today; what changes.

## 1. The writer and what it reaches [L]
- `88a06a7e(buf, size, cb)`: buf 4-byte aligned, 10000 <= size+512 <= capacity; CRC of the plaintext (`11a01fac`); `88a06a0c` reads the OTP private key (`11a05c9c`, 32 B to `0x88072de0`) into the crypto engine (`11a023a0`); header byte 13 = key found. Target = first inactive of partitions 1/2 (2 if both active); erase; per 128 sectors: encrypt **in place** (`11a02170`, polls `0x200e0104` unbounded), write, `cb(sector, total)` if non-zero, stock progress `88a1d900`; per-sector verify; header commit (sector 0) + verify; select the other partition, erase its block 0; select 0.
- **Every helper failure is retried forever** (`88a06bc8`, `88a06c48`, `88a06d62`, `88a06e28`, `88a06ee4`, `88a06fda`, `88a0703c`, `88a070c8`, `88a0710c`, `88a07136`). A failed verify jumps to `88a070be`: no commit, yet the old partition's block 0 is erased: no bootable image, dev menu (F usb-001, 036). A failed select(1) returns non-zero (`88a06b74`): "written" proves nothing, the read-back does.
- Every helper branches on the role byte `0x8807cac4` (0 = A):

| Helper | Purpose | A: local call | B: link request (ends in `889f7b12`, no bound) |
|---|---|---|---|
| `889f65f0` / `889f6624` | capacity / erase-group size | `88a086c0` / `88a0759a` | op 5 / op 3 |
| `889f683c` | select partition (EXT_CSD 179) | `88a08710` -> `88a0826c` | op 0x18 |
| `889f6a6a` / `889f6abe` | read 1 / n sectors | `88a08558` / `88a08574` | op 0x11 / 0x12 |
| `889f68d4` | erase | `88a0859e` | op 0x14 |
| `889f6eac` / `889f6e5a` | write n / sector 0 | `88a08582` / `88a08566` | op 0x16 / 0x15 |

- **On A nothing waits on the link: confirmed.** Reach of the A branches + the writer's other callees: 34 DDR functions, all `rsi.c`/`mmc_tools.cpp` (`88a0604c..88a0870e`), `88a06a0c`, progress `88a1d5f8..88a1d976`, cache flush `8803d000`; L1/ROM: log `11a01f32`/`11a01f4c`, CRC, PKTE `11a02170`/`11a023a0`, OTP read `11a05c9c`, divide `04020610`. No FORBIDDEN_TARGETS, no SPI flash/metadata/updater/companion, no OTP write (L1/ROM bodies not traversed). The RSI code is what A already runs serving B's install (`889f76ca` -> `88a08710`, F 036); its own waits are unbounded too (`88a0606c` RSI status poll, erase retry `88a085b0`).
- **Reach holes:** `88a1d900` calls through `[0x88a1f3c4]` (current stock menu, set only by `88a1d81a`, B's UI code) when non-zero, and `88a1d81a` when `B[0x88a291c0]` (set only by upgrade resume `88a29004`). Expected 0 on A [I], [U].

## 2. Calling it on A
- **Needs:** role byte 0 (A must check it: with 1 the helpers would send link requests from the slave); the writer spans CRC-checked **on A** (its own RAM copy); write window closed, no handles open (009); staging 32-byte aligned (RSI asserts, `88a07850`, `88a0813c`). yaffs may stay mounted read-only and idle: A's loop is inside the writer and A's ISRs reach no storage (007) [I].
- **The 011 risk, exactly:** the eMMC has one current partition (EXT_CSD 179, `88a08710`); yaffs and the OS's FAT use absolute sectors and never select. Left on 1 or 2, the next yaffs or FAT write lands in a boot partition (corrupting what the bootloader loads) while yaffs's view drifts from the medium (files lost, `bootloader.dev` too): both recovery paths at once. **Stock** ends writer and reader with select 0 retried forever (`88a07136`, `88a074b2`). **Today on B** [C]: `verify()` ends with up to 8 select(0); A records `selected_partition` and refuses all file classes while 1/2 is selected (`decide_install`). **On A:** nothing else runs meanwhile; afterwards A selects 0 itself and refuses storage until it gets 0. After an A reset mid-write the stock reader (every boot, before XY Forever) selects 0 first; a normal stock boot registers no yaffs device before it [F HARDWARE]. After a power cut the card resets to the user area [I JEDEC; U this card].
- **Duration:** today 36 s for 3.4 MB, 27 s of it the per-sector verify over the link (F 037); so the 77 KB product takes ~1-2 s on B, the 2.37 MB stock image (uninstall) ~25 s [I]. On A, all local: ~2-8 s for 2.37 MB [I, U].
- **Guard on A:** max count (`0xffffffff`, ~43 s at 100 MHz [U on A]) around the call, as B does today; `cb` could reload it per batch (the verify has no callback). B's guard stays normal and kicked.

## 3. Data path
- **(a) Link chunks into A's RAM** (staging `0x8b820000..0x8c000000`, 0x7e0000 B, free outside a boot freeze [C `file_boot.hpp`]): needs a new B->A bulk path; reusing write-class ops means routing by op number (ARCHITECTURE forbids); USB-only.
- **(b) A file through the engine (009 2.2d), read by A into the same staging** (4.9 MB/s [F]): no new link code; also serves menu self-update/uninstall and packages; costs one yaffs write (a cut leaves a swept tmp file). **Choose (b).**
- **A checks before the writer** (today's, [C `usb_service.cpp`]): size bounds; CRC-32 and SHA-256 equal to the host-announced values B forwards (today's PUT + token); native LDR structure and address policy; cold descriptor (install) or exact stock size + SHA (uninstall). The host keeps its checks (verified build, manifest SHA, descriptor, LDR). Then A reads back locally (header match, every sector vs staging, which now holds ciphertext) and replies status, written, partition, sectors. B waits with a bound (a barrier command as SET_DEFAULT, ~90 s), screen and USB alive; past it: `link`, "power cycle; one of the two images boots".

## 4. User-visible flows
USB install/uninstall: one host command (put file, then install job); B stays responsive; seconds instead of up to ~36 s. Uninstall can keep the stock `os.ldr` on the device, so the menu can do it later without a host. 4.7 self-update and uninstall: a menu action on a file already on the device (stage 1 has no fallback: keep the previous file). 010: `xyf-stage1` becomes "put a file, send install"; nothing else changes.

## 5. Safety, step by step (power cut or A reset)
1. Upload / file write: boot partition untouched, old boots. 2. Checks, header reads, selects: untouched. 3. Erase, data write, verify of the target: no valid target header, old boots. 4. Header commit: torn 512-B write [U, both designs]. 5. Commit done, other not erased: both valid, reader takes partition 1 (old or new). 6. Erase other, select 0: new boots. 7. Read-back, reply: new boots.
- **Same in both designs** (same function, same order). New with A: **(i) keys.** A's writer encrypts with A's OTP key; B's reader decrypts with B's. A already reads every boot image itself (`88a35b90` -> `88a34a00` -> `88a07240`, its own key) [L], so if today's header byte 13 = 1, B-written ciphertext decrypts on A and the keys are equal [I]; if 0, A must also have no key (4.3e). A mismatch = an image B cannot boot, dev menu. **Less safe than today until settled.** **(ii)** A-only faults (in-place encryption + local DMA coherency; the local path flushes, `88a0610a` -> `8803d000` [L]) would fail the verify, then stock erases without commit: dev menu. B's path is proven (037 on). **(iii)** A guard reset = a cut, plus section 2's select argument. **(iv)** B reset meanwhile: A finishes, then restarts without answering B's boot requests (existing rule; detecting a restarted B is unproven, 009).

## 6. Writer on B with a bounded wait?
No real timeout: the writer calls the helpers, which spin in `889f7b12` [L]; bounding it means patching stock code or owning the writer (007: no). Only B's guard bounds it: a reset at <= ~43 s (uninstall needs ~25 s), USB and screen frozen meanwhile, and only when the host armed it; `cb` reloads it per batch, not during the 27 s verify. Cost tiny, value small. Moving to A: ~300 lines [I] + 2.2d, and two unknowns that can cost a dev-menu recovery.

## 7. Recommendation, entry points, unproven, split
**Keep the B writer as the shipping path now** (proven; the product freezes B ~1-2 s). Build the A path with 4.7, after 2.2d, behind its own command; adopt it only after 2.6a's go/no-go; then delete the B path (B's 5 calls, `hook_logs`, guard stretch, A's install allowlist). Confidence: high that A reaches storage without the link and nothing forbidden [L]; medium that it is as safe, pending keys and coherency.

| Entry (already on `USB_CALLS`; builder rule: only from A's job) | On A | Reaches |
|---|---|---|
| `88a06a7e` / `889f65f0` / `889f683c` | writer / capacity pre-check / final select 0 | section 1 / `88a086c0` / `88a08710` |
| `889f6a6a`, `889f6abe` | header check, read-back | `88a08558`, `88a08574` |

**Unproven:** byte 13 (keys); `[0x88a1f3c4]` on A; time and guard seconds on A; PKTE/OTP on A at cold time; coherency; torn header write.
- **2.6a device safe** (automatable, no write): A reads both headers (byte 13), the active image (CRC vs header, equal to B's read), its role byte, `[0x88a1f3c4]`, `B[0x88a291c0]`, read times; select 0 confirmed; never reads `0x88072de0` (key). Done when: recorded, go/no-go decided.
- **2.6b build** (after 2.2d, 2.6a go): A's job: file read, checks, spans on A, role check, guard, writer, select 0 lock, local read-back; B's bounded barrier; host `install --via-a`. Done when: simulator covers every refusal, A silent/slow/reset, B reset, select-0 lock; builds verify; B path unchanged.
- **2.6c review:** reach, bounded B waits, select 0 before storage on every path, checks equal to today's. Done when: no open finding.
- **2.6d device risky** (user present, dev-menu kit): re-install the installed build via A, power cycle, then a new build. Done when: both boot; A time recorded.
- **2.6e build + 2.6f device safe:** install/uninstall switch to A, B path removed. Done when: device pass all PASS.
**Open questions:** (1) keep the B writer now and move it with 4.7? (2) first A-side write with the developer present (worst case: dev-menu Track 5)? (3) prove uninstall via A on the device (ends host access until a dev-menu reinstall), or rely on install?
