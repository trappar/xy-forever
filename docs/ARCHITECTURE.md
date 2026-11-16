# XY Forever architecture

How XY Forever boots, how its two halves talk, and where the code is. Product
goals: [GOALS.md](GOALS.md). On-device files: [STORAGE.md](STORAGE.md). Addresses
and stock-code facts: [HARDWARE.md](HARDWARE.md). Roadmap: GitHub issues.

Status marks: **proven** (ran on the device), **built** (offline, verified, not
device-tested), **planned**.

## Boot chain

```
power on -> real 1.2.1 bootloader (SPI flash, never modified)
         -> loads os.ldr from the NAND boot partition = XY Forever
         -> XY Forever "cold" code runs on both processors before any OS
         -> boots the chosen firmware file from /yaffs2/xy-forever/firmware/<id>/
            (no embedded OS since 088: a default that can't boot opens the menu;
             a boot that loads no image shows why and halts until a power cycle)
```

The real bootloader still handles COM at power-on (dev menu), so it stays the
last-resort recovery.

The product's outer `os.ldr` (built by `xyf_build.py`, packed by `xy_forever/outer.py`; ~99 KB) has two load records: a ROM trampoline at `0x11a00000` and the cold module at `0x8ab00000` (code window to `0x8ab80000`, 16 KiB stack to `0x8ab84000`; see "Memory map" below).
A cold descriptor carries the CRCs of the stock helpers the module calls (payload size 0). The other, offline modes still embed the stock OS payload at `0x8b000000` as a third record (`NO_EMBEDDED_MODES`).

## Memory map (product, build 171)

Table and owners: [HARDWARE.md](HARDWARE.md#memory-map-product-build-171). The module (code window `0x8ab00000..0x8ab80000`), its stack (`..0x8ab84000`) and the dormant stage-2 window (`0x8ab90000..0x8ac00000`) lie in `0x8aa65bf4..0x8b000000`, above the stock allocator arena and below B's mod job region (5.4d2); `xyf_build.py` `check_memory_map` (build and `--verify`) fails the build if either layout leaves that gap or touches the boot source, job region, staging, framebuffer or report slots.
- **Free at power-on:** outside every static bootloader segment and the arena (both build-checked); the 1.2.1 bootloader's disassembly loads no immediate in `0x8a000000..0x8c000000` but its stream buffer `0x8a000000`, and its initialized data holds no aligned pointer into `0x8ab00000..0x8ac00000` (one byte-table word at `0x1190029c` looks like one); its load path reads at most 8 MiB into `0x8a000000` (`88a34a12`) and then places our two records. Our own buffers are all elsewhere; peer DMA (`owned_buffer_range`) refuses `0x8ab00000..XYF_STACK_END`. Never shown on the device: a cold-time write above `0x8ab20000` (171's first boot is the first).
- **Hand-off:** the image is copied from staging to `0x8a000000` (at most `staging_bytes` 0x7e0000 B, so below `0x8a800000`); every record is admitted by `cold.cpp`'s policy, whose protected range `0x8ab00000..XYF_STACK_END` covers the running code and stack, before the stock RAM loader (also protected) places it. Stock 1.2.1 (2,373,336 B), 1.2.5's `os.ldr` and a device-assembled mod image (2,493,876 B) place nothing in the gap (`test_official_images_pass_the_product_policy`); any image up to the 8 MiB boot source meets the same check, so size does not matter.

## Processors

| | Processor A (role 0) | Processor B (role 1) |
|---|---|---|
| Owns | NAND storage (yaffs) | screen, keys (PSoC), USB |
| Enters XY Forever | about 0.5 s after B | first |
| Link role | SPI slave (server) | SPI master (client) |

A decides nothing on its own: it serves B and boots what both agreed. B makes
every user-facing decision.

## The link between the processors (stock SPI request protocol, reopened at cold time)

1. **Handshake** (proven, Test 023). B sends stock request 1 (reply `0xfedebeda`),
   request 10 (reply `0xedcba987`), then release 7.
2. **Held link** (proven, Test 024). B sends op-10 keepalives every 250 ms; A treats any new request
   header in its RX buffer (`0x11b000c0`) as activity (idle window 3 s; 10 s for the product). Product
   sessions have no cap (`peer_services::no_cap`): the cold millisecond clock (`target/xyf_clock.hpp`) is
   wrap-safe, so the menu and USB stay up indefinitely. Before 086 the old formula overflowed after ~524
   s, which is why sessions were capped at 300 s. Op-10 replies are computed by the stock ISR, so A → B
   data always uses the image-transfer layer.
3. **Commands** (`'CM' << 16 | item << 8 | seq`). Two transports:
   - **Requests** (boot session; proven since 068). B sends the value as
     argument 0 of a stock op 0x18 request (`Stock121PeerSession::ask`). The stock
     ISR only queues such a request, so it waits until A's loop takes it. A answers
     `~value` itself and acts on the command before it polls again. B may therefore
     send a command while A is busy (mounting, reading an image): it is answered
     late, never lost. B waits up to 15 s (`xyf_boot::command_ms`).
   - **Op-10 arguments** (keepalives; commands of the removed v2 flow; proven). A only
     samples the header buffer, so a command is lost if another header follows
     before A polls again. That is safe only while A's loop is polling. It was the
     cause of the 064/066 link failures (HISTORY, Diag 076), which is why the boot
     session moved to requests.
4. **Image transfer** (proven). A freezes an admitted image (or a small blob) and serves it through stock
   partition/sector ops 0x18/0x11/0x12; B receives and verifies it. Product boot transfers carry no
   SHA-256 (087, user decision): the image is identified by size and CRC-32 (`info.txt`), and each copy
   is CRC-checked once (089): A's file read at CHECK, B's received bytes as they arrive, each boot copy
   at hand-off. Any failure means no boot (menu, or the halt screen).
5. **Storage server** (proven, Tests 027–050, 097–139). A runs B's file requests with the stock
   executors through one file engine (build 155, plan 2.2a; `target/file_engine.cpp`, decision 009):
   every request passes `decide_request` and its path policy `decide_engine`, which B also applies
   before it sends. B's timed client (158, 2.2b; `target/file_client.cpp`) uses the stock builders on
   the session's one slot, never the stock RPC; every wait bounded (5 s, 45 s for a change: then `link`;
   still out after 60 s: link lost, B stays at the menu: item 6).
   Changes share one read-write window (163, 2.2c): A opens it on the first change; B closes it (op 9)
   8 s after its last write and before install, set default, CONTINUE, REBOOT; A after 10 s idle, before
   other command items, when B is gone; A then rescans, B fetches the catalog. Refused: removing or
   renaming away `/yaffs2` or `/yaffs2/bootloader.dev` (also via links), anything outside `/yaffs2`, bad
   paths, the raw boot-partition layer (install mode only), all while a boot partition may be selected.
6. **Lone restarts** (plan 0.9b, record 016; built 159): a restart of one processor becomes a restart of
   both, in the stock order (A's bootloader serves B's boot). A never answers B's stock bootloader: its
   link open (op 10 `0x12345678`) after B's first command, or a boot request (select, sector read) with
   nothing frozen outside install mode, is held unanswered; A closes storage, restarts, and its bootloader
   answers the held request (Test 108). A also follows when B is silent past its idle window. Since 164
   (0.9k2, record 022) B never resets itself on a lost link (menu, USB, `link`), A has no guard of its
   own and never restarts by a timer of its own; keepalives are plain op-10 commands.

## Boot flows (product)

- **Plain power-on:** file-boot session → A checks the default library entry
  (`info.txt` size/CRC, LDR policy) → serves it → both boot it. No USB, never waits
  for a computer (Test 060). A default that can't boot opens the menu with the
  reason (Test 059).
- **PLAY at power-on:** the boot menu with USB on (Test 064). Before 068 this failed
  when A's storage mount was slow (HISTORY, Diag 076).
- **USB service:** entered after XY Forever's own reboots (reboot marker at SDRAM
  `0x8bfff000`); host commands in [OPERATING.md](OPERATING.md).

## Boot model v3 (proven)

| Situation | Behaviour |
|---|---|
| Plain power-on | straight to the default OS, as fast as possible; no USB |
| Default can't boot | boot menu (with the reason) |
| Power-on with PLAY | boot menu with USB on |
| Agent reboots | to the OS or to the menu only (held, USB-ready, guard carried) |
| Agent attached to the menu | menu and USB service are one: every command works from the menu, never a reboot to switch (a transient "working" screen is fine) |
| Menu timeout | never |

Implementation: **one link session per boot** (`XYF_ONE_SESSION`). A keeps one fresh read-only storage
session for the whole link session and unmounts only right before an image is frozen for the boot (a
second `begin_fresh` in one boot is `busy`: the constructed yaffs device stays in the stock device
list). While nothing is frozen, B's file requests are routed to the storage server; while a transfer is
frozen, the image protocol runs. Routing is by "frozen?", never by op number (0x18/0x11/0x12 are also
install ops). On B the menu is the home loop: screen, keys, and the USB pump with the full command set;
command items to A go through the current session (`b_send`).

Code: `target/usb_service.cpp` (`home_*`), `file_boot.cpp` and `key_ui.cpp` (`XYF_ONE_SESSION`),
`storage_server.cpp`, `peer_services.cpp` (RPC mode), `menu_link.cpp`. **Status:** 065/066 failed on
hardware (HISTORY). With the request commands v3 is proven: home, reads, dry run and reboots (069, 075),
writes and set default without a reboot (080, 081), install from home (075).

## Writes (proven, Tests 048–050)

yaffs rename is not reachable, so state uses power-safe A/B slots (`state-a.txt` / `state-b.txt`).
Sequence on A: read-only unmount → stock callbacks → read-write mount → write ≤ 4 KiB → read back →
read-write unmount → gates back → read-only remount. Writes are exact-path checked and happen only on an
explicit user/host action, never on a normal boot. The boot partition is written only by the stock
writer `88a06a7e` (commit-last A/B, power-loss safe;
record 012).

The menu's own set-default (SHIFT+PLAY, `XYF_STATE_WRITE`, `target/xyf_state_write_library.cpp`) was
proven in Test 030 but is not in the product: stage 1 is full. It is meant to return in stage 2.

## Simulator (plan 1.3; offline)

The device's own C++ on the Mac, speaking the protocol on 127.0.0.1 (raw byte stream, no
framing of its own). Code reaches the device only after it passes here (ORCHESTRATION.md).

- **Hosted** (compiled from `target/`, rewritten mechanically by `xy_forever/sim.py`: casts through emulated
  memory, `stock<>` calls to `sim/stock.cpp`, asm to host calls; a rewrite that stops matching fails the build):
  `usb_service`, `usb_proto`, `storage_server`, `menu_link`, `file_boot` (both roles), `peer_services` (incl.
  `poll`), `cold_storage`, `retained_lookup`, `cold_heap`, the library/catalog/state modules, `native/` ldr, hash,
  peer_boot, trace_slot. Each processor is a process; the stock 1.2.1 image (private) is in its memory, so every
  identity CRC checks real bytes; SDRAM survives a processor reset.
- **Stood in for** (`sim/`): the stock calls (USB CDC = the TCP port, open only while USB is on, so a restart
  drops the client and the port comes back; the SPI link = a board relay, A's stock ISR answers generic ops and
  queues class requests in the pool slot; the stock request builders B's client uses; A's executors; yaffs = a
  host folder with read-only/read-write mounts and media gates; the boot partition = a file, two slots, header
  last, the other erased, no encryption), the retained cold state, `cold.cpp` (its policy is extracted; an OS
  start stops both processes: `os`; no image: `halted`) and `key_ui` (the menu loop without screen or keys:
  `home_pump`, tick, link check; SCREEN shows a test pattern, KEY does nothing).
- **Resets** (`sim/boot.cpp`, record 022): WDOG0 at host calls; a stock reset always lands in the bootloader stand-in. B's probes A
  first: on only if A reset <= `boot_a_window_ms` (400; device 0.15-1.4 s) before B or later, else waits (`b_waits_a`).
- **Not hosted:** the menu's screen and keys, os.ldr itself, CDC ring and USB timing, NAND timing, encryption,
  stage 2; yaffs is a (case-insensitive) host folder.
- **Run:** `python3 -m xy_forever.sim` (`SESSIONS/simulator/` in the private folder: seeded `yaffs2/`, `bootpart.bin`,
  `builds/`; `--port`, `--plain`, `--build N`, `--pre-v1`), then `xyf_host.py --sim 127.0.0.1:7341` (or `XYF_SIM`),
  `scripts/device_pass.py` / `scripts/conformance.py --sim …` (evidence removed when nothing failed, unless `--keep`).
- **Control** (port + 1, one line, JSON reply): `status` (A's `partition`; `boot_writes`, yaffs changes on a boot
  partition; `rw_open`/`unclean_mounts`, read-write mounts never unmounted; `to_a`/`to_b`, link messages relayed),
  `power [play|plain]`, `cut`, `set NAME N` with `a_silent`, `a_slow_ms`, `drop_request`, `drop_body`, `cable_pull`
  (bytes), `space`, `fail_write` (the Nth write, boot partition too), `fail_mount`, `vanish_marker`, `cut_after`,
  2.2b: `fail_select` (switches, then fails), `fail_unmount` (while set), `fail_open` (A's), `full_path` (240 B, no NUL)
  (storage operations, then a power cut and a PLAY power-on); `swap PATH` (stock path buffer); `inject CLASS OP ARG0
  ARG2 [HEX]` sends A a raw request past B's checks (class 0: generic), `injected TAG` reads A's answer
  (`Simulator.ask_a`); `advance MS` moves both processors' cycle counters ahead, so a device time-out (A's idle
  window, a held window) passes unwaited (`Simulator.advance`, in steps); `reset a|b`, `expire a|b` (if armed),
  `set a_boot_ms|b_boot_ms|a_redeliver|boot_a_window_ms|a_stall_now`. Tests: `tests/test_simulator.py`, `tests/test_guard_reset.py`.

## Self-update and uninstall

Stage 1/2 is built, not device-tested.

- **Stage 1** (boot partition): small, rarely changed; hold-key entry, link, a minimal recovery menu, loads stage 2. It is at its size limit (49140/49152 B in the narrow layout), so new features go to stage 2.
- **Stage 2** (`/yaffs2/xy-forever/stage2/`): window `0x8ab90000..0x8ac00000` (dormant: no file); one
  audited trampoline (`xyf_stage2_enter`); ABI 1 = hello, ABI 2 = the menu. Format:
  `target/xyf_stage2.hpp`, `xy_forever/xyf_stage2.py`. Builds: `--xyf-stage2`,
  `--xyf-stage2-menu`.
- **Stock MTP** (the product's menu item 091-150; the offline `--xyf-mtp` build went in
  0.4b): stock MTP requests run in B's USB ISR. Abandoned (HISTORY, "Stock MTP"); removed
  from the product in 151 (the MTP row says "not available yet"); our own MTP
  responder replaces it (milestone M3).
- **Uninstall** (USB command `UNINSTALL`; proven on 087): rewrites the boot partition
  with the exact stock 1.2.1 `os.ldr` (size + SHA checked). It deletes nothing.

## Build modes (`xyf_build.py`)

| Flag | What | Use |
|---|---|---|
| `--xyf-product` | the product with boot model v3 (wide layout, USB service, menu, file/library boot, no embedded OS) | installs (build 090) |
| `--xyf-stage2` / `--xyf-stage2-menu` | stage-1 + stage-2 split (Tests 032/034) | offline |
| `--verify` | re-check an existing build dir (mode from its manifest) | every install |

`scripts/neutral.py [REF]` builds every mode from a git ref and from the working tree and compares the
candidates (for changes that must not alter the firmware). Builds are deterministic. Only the modules in
`GC_MODULES` (`xyf_build.py`) get section garbage collection, so unreferenced code in the others is
linked into the product. The pre-v3 flow and the stock MTP code went in plan 0.4 (decision record 001);
the never-running `activated_` branches in `cold_storage.cpp` stay because removing them changes the
candidate. Never linked, kept for planned features: `boot_services.cpp`'s update-key reader (calls stock
L1 wrappers; superseded by `otp_keys.cpp`) and most of `native/`. The verifier re-checks every CRC span,
every stock call target and every symbol rule; see "Safety rules" below.

### Feature switches

The C++ is one source tree compiled with different `-DXYF_…` switches per mode.
`features(mode)` and the mode tuples near the top of `xyf_build.py` decide them.

| Switch | Modes | What it adds |
|---|---|---|
| `XYF_FILE_BOOT` | product, stage2, stage2-menu | file/library boot with the menu inside its session (`file_boot.cpp`, `xyf_boot.hpp`) |
| `XYF_MENU_LINK`, `XYF_REMOTE_READ` | product | the USB service's command link (`menu_link.cpp`), and A's storage server |
| `XYF_USB_SERVICE` | product | the USB service on B |
| `XYF_STAGE2`, `XYF_STAGE2_MENU` | stage2, stage2-menu | the stage-2 loader; ABI 2 with the menu in stage 2 |
| `XYF_MTP` | product | A's MTP executor and B's 097 self-test (`mtp-test`) |
| `XYF_TFW_CHECK` | product | `tfw.check` (4.3c): B reads its OTP keys (`otp_keys.cpp`) and checks a `.tfw` (`native/tfw_check`) |
| `XYF_USB_SET_DEFAULT`, `XYF_USB_FILE_WRITE`, `XYF_PLAIN_IMAGES`, `XYF_SERVICE_SCREEN`, `XYF_USB_MENU`, `XYF_MENU_ON_FAILURE`, `XYF_SERVICE_ON_MARKER_ONLY`, `XYF_REBOOT_TARGETS` | product | one product step each (Tests 048–064), all proven on hardware in build 064 |
| `XYF_ONE_SESSION` | product | boot model v3 (proven since 069/075) |
| `XYF_NO_EMBEDDED` | product | no embedded OS; `xy_forever_no_image()` halt screen |
| `XYF_STATE_WRITE` | none | the menu's own set-default; parked until stage 2 |

Every product switch is on in exactly one mode; folding them waits for M2 (decision 001,
question 3). Host tests compile `usb_service.cpp` and `storage_server.cpp` with subsets.

## Code map

| Path | What |
|---|---|
| `xyf_build.py` | build + verify a candidate `os.ldr` (all audits) |
| `xyf_host.py` | host tool for XY Forever's USB service (stdlib only) |
| `ui_preview.py` | render the device UI to PNG on the host (same C++) |
| `verify.py` | cross-check the native LDR parser against official images |
| `xyf_package.py` | review and stage a decrypted official ZIP on the host (HARDWARE, `.tfw`); `extract-os`: OS-only library entry from an official `.tfw`, run by the device's own extractor compiled for the Mac (`host/tfw_extract.cpp`, `xy_forever/tfw_extract.py`) |
| `target/cold.cpp`, `storage_probe_entry.s` | cold entry on both processors; boots the loaded library image |
| `target/xyf_clock.hpp` | wrap-safe millisecond clock for every cold module |
| `target/file_boot.cpp`, `xyf_boot.hpp` | file/library boot session on both roles; menu boot |
| `target/menu_link.cpp`, `peer_services.cpp` | USB command link (file boot's session); stock SPI peer session wrappers |
| `target/key_ui.cpp` | B's hold-key UI loop, keys from the PSoC, screen present |
| `target/usb_service.cpp`, `usb_proto.cpp` | USB CDC service on B (install, files, reboot, screen, key) |
| `target/storage_server.cpp`, `file_engine.cpp`, `file_client.cpp` | A's storage server (the file engine's entries and window, install mode); the engine's path policy and request decision (both processors); B's timed file client |
| `target/cold_storage.cpp`, `boot_services.cpp`, `retained_lookup.cpp`, `cold_heap.cpp` | cold-time yaffs session, stock fs prefixes, retained device lookup, allocator guard |
| `target/xyf_library.cpp`, `xyf_cold_library.cpp`, `xyf_catalog.cpp`, `xyf_store.cpp`, `xyf_layout.cpp`, `xyf_info.cpp` | library scan, catalog blob, key=value and `layout.json` parsers |
| `target/xyf_state*.cpp` | A/B state slots and the set-default write |
| `target/xyf_menu.cpp`, `xyf_menu_view.cpp`, `ui_*.cpp` | menu state machine, pages, drawing (own font/logo) |
| `target/census.cpp`, `warm_snapshot.cpp`, `storage_probe_record.cpp`, `storage_probe_reporter.*` | diagnostic census + report written after the OS mounts storage |
| `target/*_spans.hpp` | CRC spans of stock code each feature calls (generated by `re/spans.py`) |
| `target/xyf_stage2*`, `stage2_*.cpp` | stage-2 loader and images |
| `native/` | freestanding codecs: LDR, SHA-256, inflate/zip, AES/TFW, package admission, OS-only `.tfw` extractor (`tfw_os`, 003) and P-224 signature check (`p224`, `tfw_signature`), content, companions, BLE session, peer boot, `tfw.check`'s verdict (`tfw_check`, linked with `tfw`, `p224`, `aes`), the mod linker (`mod_link`, `mod_elf`, `mod_numbers`: core.mjs `linkKit` in file steps, 5.4b), mod assembly's patch and content steps (`mod_patch`, `mod_content`, 5.4c; they read the linker's result, 5.4i); host-tested, the rest not linked yet |
| `xy_forever/` | Python: build helpers (`compile.py`, `outer.py`, `toolchain.py`), report decoder (`census.py`), USB protocol, library entries, stage-2 packer, private paths (`paths.py`), mod packages from an open-xy kit (`modkit.py`, 5.4a; STORAGE.md), the native linker's host proof (`modlink.py`), the native patch/content steps on the host (`modpatch.py`, `host/mod_assemble.cpp`) |
| `re/` | reverse-engineering tools over the private 1.2.1 listing (reach, MTP reach, CRC spans) |
| `scripts/` | device session helpers (`keepalive.sh`, `dryshow.py`, `capture_session.py`); `neutral.py` (candidate comparison); `modkit_core.mjs`, `modlink_core.mjs` (run open-xy's `core.mjs` for `modkit.py`, `modlink.py`; `link_out` writes its link result) |
| `sim/`, `xy_forever/sim.py` | the device simulator (above) |
| `protocol/v1.json`, `scripts/protogen.py` | the serial protocol spec and its generator: `target/usb_proto_ids.hpp`, `xy_forever/usb_proto_ids.py`, `clients/ts/src/v1.ts`, `docs/PROTOCOL.md` |
| `clients/ts/` | TypeScript client for the web app (decision 013): generated `v1.ts`, call layer, Node TCP and Web Serial transports; `npm test` (Vitest, simulators started by the tests); `clients/ts/README.md` |
| `tests/` | `python3 -m unittest discover -s tests` (host-compiled C++ bridges + Python) |

## Mods (open-xy): offline findings (nothing ran on the device)

- Upstream release `v0.0.6` (`52c8de9`) and its public kit (`opxy-browser-kit/1`: normalized Blackfin
  relocatable objects, ownership/fallback metadata, a browser linker) let a selection be linked without a
  compiler. An offline replay matched GNU ld byte for byte (superseded by `modkit.py` and `modlink.py`).
  FM2OP + Reaction: 146,948 initialized bytes; the reserved region is 2 MiB.
- Select at boot and restart after a menu change: upstream has no stable binary ABI, so no live unloading.
  A kit is an ABI generation (collection-generated tables): pin profile, linker rules and object hashes together.
- The kit JSON (21.6 MB, base64 ELF and artwork) is not for the device: a native package must split link
  data from UI data.
- Upstream links its module at `0x8d2d0380`; XY Forever runs at `0x8ab00000`. The diagnostic reporter range
  `0x8d4ce380..0x8d4d0380` falls in upstream's relocated-table zone (reported, unchecked).
- Every selection changes `content/op1_factory.json` (and screens, presets): library switching alone does
  not activate matching content. No implicit boot-time writes.
- Project companions need a matching project size and CRC and exclude players; a missing engine falls back
  to Prism, a missing effect to none.
- A private relocation kernel (resolved fixups only; ELF types 6, 7, 10, 13, 18) matched GNU output on the
  host; its Blackfin build was compiled, never run.

## Safety rules (all code)

- Every stock function XY Forever calls is CRC-checked against the authenticated
  1.2.1 bootloader (`*_spans.hpp`, `CONSTRUCT_SPANS`, `DIRECT_SPANS`, key UI, peer and
  server spans); the build re-checks each CRC.
- Calls go through noinline wrappers or direct calls; the builder's call audit rejects any
  target it cannot resolve, every forbidden target (SPI flash and metadata, updater, companions,
  stock `.tfw` pipeline, the unbounded stock RPC `889f869e`/`889f7b12`) and any boot-ROM entry
  but memcpy and the OTP read/init (`tfw.check` only). Scoped groups: the five request builders
  only from `file_client`, A's posix stat/open/close only from the marker check, rename only A's.
- Every loop is bounded, and every failure falls back to a bootable state.
- No data loads from L1 instruction memory. No implicit BSS; no memcpy, memset or
  division helpers (`test_xyf_freestanding`).
- The real bootloader is never written. The 1.2.5 bootloader is never installed.

The role-A dispatcher snapshots the pending request pointer once per poll. All
command, storage-RPC and image-transfer checks use that snapshot. An interrupt
arrival after the snapshot waits for the next poll; re-reading the pointer in
each branch let late storage requests bypass RPC dispatch and fail the image
protocol (069 install stalls). `tests/test_peer_dispatch.py` exercises the
production dispatch body with controlled arrivals.
