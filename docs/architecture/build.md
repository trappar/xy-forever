# XY Forever architecture: self-update and build modes

Part of [XY Forever architecture](../ARCHITECTURE.md).

## Self-update and uninstall

Stage 1/2 is built, not device-tested.

- **Stage 1** (boot partition): small, rarely changed; hold-key entry, link, a minimal recovery menu, loads stage 2. It is at its size limit (49140/49152 B in
  the narrow layout), so new features go to stage 2.
- **Stage 2** (`/yaffs2/xy-forever/stage2/`): window `0x8ab90000..0x8ac00000` (dormant: no file); one
  audited trampoline (`xyf_stage2_enter`); ABI 1 = hello, ABI 2 = the menu. Format:
  `target/xyf_stage2.hpp`, `xy_forever/xyf_stage2.py`. Builds: `--xyf-stage2`,
  `--xyf-stage2-menu`.
- **Stock MTP** (the product's menu item 091-150; the offline `--xyf-mtp` build was
  removed): stock MTP requests run in B's USB ISR. Abandoned (HISTORY, "Stock MTP"); removed
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
linked into the product. The pre-v3 flow and the stock MTP code were removed (decision record 001);
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
| `XYF_TFW_CHECK` | product | `tfw.check`: B reads its OTP keys (`otp_keys.cpp`) and checks a `.tfw` (`native/tfw_check`) |
| `XYF_USB_SET_DEFAULT`, `XYF_USB_FILE_WRITE`, `XYF_PLAIN_IMAGES`, `XYF_SERVICE_SCREEN`, `XYF_USB_MENU`, `XYF_MENU_ON_FAILURE`, `XYF_SERVICE_ON_MARKER_ONLY`, `XYF_REBOOT_TARGETS` | product | one product step each (Tests 048–064), all proven on hardware in build 064 |
| `XYF_ONE_SESSION` | product | boot model v3 (proven since 069/075) |
| `XYF_NO_EMBEDDED` | product | no embedded OS; `xy_forever_no_image()` halt screen |
| `XYF_STATE_WRITE` | none | the menu's own set-default; parked until stage 2 |

Every product switch is on in exactly one mode; folding them waits for M2 (decision 001,
question 3). Host tests compile `usb_service.cpp` and `storage_server.cpp` with subsets.
