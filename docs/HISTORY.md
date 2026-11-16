# Lessons

What failed on hardware (or in the work around it) and the rule it produced, one line
each. Numbers are device test or build numbers, as cited by records and reference docs;
the evidence behind them stays private and in git history.
A = storage processor (role 0); B = screen/keys/USB/PSoC (role 1).

## Boot and cold start

- 001: a chained `os.ldr` in the NAND boot partition works and the COM dev menu stays intact: keep the real bootloader untouched.
- 002: too many changes in one build stalled the logo with no clue why: one change per device test.
- 005: a failed cold build also broke the dev-menu upload; only the official `.tfw` recovered it: it is the last-resort recovery.
- 002/005/006 (fixed in 008): data loads from L1 instruction SRAM (0x11a01d96) stall the boot: never read L1I as data.
- 009: no YAFFS device is registered at cold entry on a normal boot: XY Forever constructs its own.
- 010/011: a constructor span differed on the device only by the clock global 0x88a2ef28: CRC-gate stock code and report the differing word before calling it.
- 012/013: stock stdio needs a provider registry filled only by a writable mount: use direct yaffs calls (provider vtable +0x10 write, +0x14 read).
- 016: both processors run `os.ldr`; the other one is refused at the role byte 0x8807cac4: every path needs a peer side.
- 018: `ms_since` returned µs (timer at 400 MHz): check time units on the device. 086: the cold ms formula overflowed at ~524 s: use wrap-safe clocks.
- 019/020: the vsync callback is not registered at cold time: present synchronously like stock, clear the framebuffer first.
- 022: the GPIO vector lives in L1 scratch 0x11b00000 and the PA2 role strap reads 0 at cold time: take the role from the role byte.
- 053: the official 1.2.5 `os.ldr` boots from the library on the 1.2.1 bootloader; the 1.2.5 bootloader disables the dev menu: never install it.
- 055/056: older firmware (1.1.33) boots "pretty broken" without its own content: firmware and content go together.
- 087/088/090: plain power-on ~7.15 s (087), ~5.84 s (088), stock 1.2.1 ~4.6 s; BOOT to hand-off 369 ms (090): size waits to these known times, never minutes.
- 087: the first boot after a dev-menu MTP copy rescans yaffs (16.85 s once): size boot waits for a full scan.
- Storage after an OS crash: A's read-only mount can fail until yaffs repairs itself: mount read-write once to repair, only on that failure.

## The A↔B link

- 023/024: the cold A↔B link works and B can hold A and send it commands; B enters ~0.53 s before A.
- 041/042: ranged reads leaked A's stock request slots until each ended with a 1-byte probe: release every stock request.
- 043/044: a second session right after a released one never reached A: wait before a new session.
- Diag 075/076: op-10 commands are only sampled by A and are lost while A is busy (a slow storage mount), not because PLAY was held: commands that matter are queued requests (068).
- 069: A's dispatcher re-read the pending request pointer in each branch, so a late request reached the wrong handler: read shared pointers once per poll (`tests/test_peer_dispatch.py`).
- 076: a build batching five unqualified changes could not be qualified: one change per build, each qualified.
- 117-130: a reply missing a body the requester always expects freezes B for good: refusals use the stock reply shape.
- 022: a restart of B alone freezes the device: never restart one processor alone; prevent hangs with bounded waits.

## USB service and installs

- 032: a stale stock CDC callback (11a03e04) drained the receive ring: clear it while serving.
- 033/034: the stock provider registry is `{0, begin, end, capacity}`; the CDC ring is 0x800 B: 1 KiB chunks.
- 035: `begin()` refuses session caps above 600 s.
- 036: stock partition select returns 0 on success, and a failed writer verify erases the old partition's block 0: check return conventions before any boot-partition write.
- 038: replies above 1 KiB hung B; the bootloader starts USB only on its charging path (057: B starts USB itself).
- 040: an SDRAM marker survives a soft reset; RAM does not survive a power cycle: a freeze breadcrumb must be drawn on the screen.
- 045: hold + guard left on with no host gave a reset loop, and a stock reset with our guard armed fails: disarm before reset, never leave hold on unattended.
- 064/066: switching menu and service by reboot left no USB route back once the link failed: one link session per boot, with the menu reachable.
- 095: a 1 KiB read of SCTL/SSTAT hung B: read only registers stock reads (SAFETY, "Register reads").
- 104-108: a reset with USB running freezes B; the watchdog guard never recovered a hang inside MTP, and 171's guard test missed: the guard is removed.
- MTP probe builds: stopping and restarting USB functions inside one boot froze B twice: USB starts once per boot as one composite device (records 030, 031).
- Record 031: our config record was 8 bytes short, so nothing appeared on the bus: compare descriptors byte for byte with stock.
- 1.2.1 and 1.2.5 content are identical; the 1.2.5 OS has no SPI or upgrade-tree writes.

## Stock MTP (builds 091–150): abandoned

Stock MTP worked with OpenMTP (138) but runs inside B's USB interrupt and blocks on A with
no timeout, so a stall cannot be recovered from our code (recovery watchdogs 140–150 all
froze), and every failed attempt cost a power cycle. Replaced by our own MTP responder on
our file engine. Do not retry.

## Process

- An agent ran only the tests it touched and left five suite failures: every brief asks for the full suite.
- A branch never built for Blackfin failed to link after merging: every merge gets a product build.
- Agents past 200-300k tokens read big files whole: briefs give line maps and ranges.
- Worktrees left behind filled the workspace: remove a worktree as soon as its agent is done.
- zsh does not split an unquoted variable, so ids reached the host tool as one word: quote or use arrays (`mods apply` now splits at spaces and commas).
- A host tool that sends HELLO while the device is in TE's dev menu is unsafe: stop every host tool first.
- After stopping the keepalive wait 2 s; before a full device pass check that `content-state.txt` says official, else use `--stages protocol`.
