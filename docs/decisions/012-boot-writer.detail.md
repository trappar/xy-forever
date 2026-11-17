# 012: Boot-partition writer on processor A (detail)

Detail of [012-boot-writer.md](012-boot-writer.md).

## 2. Calling it on A
- **Needs:** role byte 0 (A must check it: with 1 the helpers would send link requests from the slave); the writer spans CRC-checked **on A** (its own RAM
  copy); write window closed, no handles open (009); staging 32-byte aligned (RSI asserts, `88a07850`, `88a0813c`). yaffs may stay mounted read-only and idle:
  A's loop is inside the writer and A's ISRs reach no storage (007) [I].
- **The 011 risk, exactly:** the eMMC has one current partition (EXT_CSD 179, `88a08710`); yaffs and the OS's FAT use absolute sectors and never select. Left on
  1 or 2, the next yaffs or FAT write lands in a boot partition (corrupting what the bootloader loads) while yaffs's view drifts from the medium (files lost,
  `bootloader.dev` too): both recovery paths at once. **Stock** ends writer and reader with select 0 retried forever (`88a07136`, `88a074b2`). **Today on B**
  [C]: `verify()` ends with up to 8 select(0); A records `selected_partition` and refuses all file classes while 1/2 is selected (`decide_install`). **On A:**
  nothing else runs meanwhile; afterwards A selects 0 itself and refuses storage until it gets 0. After an A reset mid-write the stock reader (every boot,
  before XY Forever) selects 0 first; a normal stock boot registers no yaffs device before it [F HARDWARE]. After a power cut the card resets to the user area
  [I JEDEC; U this card].
- **Duration:** today 36 s for 3.4 MB, 27 s of it the per-sector verify over the link (F 037); so the 77 KB product takes ~1-2 s on B, the 2.37 MB stock image
  (uninstall) ~25 s [I]. On A, all local: ~2-8 s for 2.37 MB [I, U].
- **Guard on A:** max count (`0xffffffff`, ~43 s at 100 MHz [U on A]) around the call, as B does today; `cb` could reload it per batch (the verify has no
  callback). B's guard stays normal and kicked.

## 3. Data path
- **(a) Link chunks into A's RAM** (staging `0x8b820000..0x8c000000`, 0x7e0000 B, free outside a boot freeze [C `file_boot.hpp`]): needs a new B->A bulk path;
  reusing write-class ops means routing by op number (ARCHITECTURE forbids); USB-only.
- **(b) A file through the engine (009, #4), read by A into the same staging** (4.9 MB/s [F]): no new link code; also serves menu self-update/uninstall and
  packages; costs one yaffs write (a cut leaves a swept tmp file). **Choose (b).**
- **A checks before the writer** (today's, [C `usb_service.cpp`]): size bounds; CRC-32 and SHA-256 equal to the host-announced values B forwards (today's PUT +
  token); native LDR structure and address policy; cold descriptor (install) or exact stock size + SHA (uninstall). The host keeps its checks (verified build,
  manifest SHA, descriptor, LDR). Then A reads back locally (header match, every sector vs staging, which now holds ciphertext) and replies status, written,
  partition, sectors. B waits with a bound (a barrier command as SET_DEFAULT, ~90 s), screen and USB alive; past it: `link`, "power cycle; one of the two images
  boots".

## 4. User-visible flows
USB install/uninstall: one host command (put file, then install job); B stays responsive; seconds instead of up to ~36 s. Uninstall can keep the stock `os.ldr`
on the device, so the menu can do it later without a host. Stage-2 self-update and uninstall: a menu action on a file already on the device (stage 1 has no
fallback: keep the previous file). 010: `xyf-stage1` becomes "put a file, send install"; nothing else changes.

## 6. Writer on B with a bounded wait?
No real timeout: the writer calls the helpers, which spin in `889f7b12` [L]; bounding it means patching stock code or owning the writer (007: no). Only B's
guard bounds it: a reset at <= ~43 s (uninstall needs ~25 s), USB and screen frozen meanwhile, and only when the host armed it; `cb` reloads it per batch, not
during the 27 s verify. Cost tiny, value small. Moving to A: ~300 lines [I] + #4, and two unknowns that can cost a dev-menu recovery.
