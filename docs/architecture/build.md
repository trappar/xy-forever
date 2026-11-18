# XY Forever architecture: self-update and build modes

Part of [XY Forever architecture](../ARCHITECTURE.md).

## Self-update and uninstall

The shipping product is one larger image. Stage 2 is a dormant, offline-tested option,
not an installed dependency: a normal boot reads no stage-2 file. ABI 3's file format,
window, first-HELLO load and services are described in [package files](../storage/packages.md).
The legacy narrow-layout modes retain ABI 1 (hello) and ABI 2 (menu) demonstrations.
No trial counter or rollback flow is implemented in the shipping product.

**Uninstall** (USB command `UNINSTALL`, proven on the device) rewrites the boot partition
with the exact stock 1.2.1 `os.ldr` (size + SHA checked). It deletes nothing.

Stock MTP runs storage requests inside B's USB ISR with no bounded wait on A. It is
abandoned; our MTP responder uses the file engine from the main loop.

## Build modes (`xyf_build.py`)

| Flag | What | Use |
|---|---|---|
| `--xyf-product` | the product with boot model v3 (wide layout, USB service, menu, file/library boot, no embedded OS) | installs |
| `--xyf-crash-trigger` | device test (#102): the product plus `diag.crash` (0x63), a fault on purpose for the crash screen (`scripts/crash_trigger.py`); never the product (its verifier refuses the trigger) | that test only |
| `--xyf-stage2` / `--xyf-stage2-menu` | stage-1 + stage-2 split (offline demos) | offline |
| `--verify` | re-check an existing build dir (mode from its manifest) | every install |

`scripts/neutral.py [REF]` builds every mode from a git ref and from the working tree and compares the
candidates (for changes that must not alter the firmware). Builds are deterministic. Only the modules in
`GC_MODULES` (`xyf_build.py`) get section garbage collection, so unreferenced code in the others is
linked into the product. The pre-v3 flow and the stock MTP code were removed;
the never-running `activated_` branches in `cold_storage.cpp` stay because removing them changes the
candidate. Never linked, kept for planned features: `boot_services.cpp`'s update-key reader (calls stock
L1 wrappers; superseded by `otp_keys.cpp`) and most of `native/`. The verifier re-checks every CRC span,
every stock call target and every symbol rule; see "Safety rules" below.

### Stage-2 image audit

`xy_forever/stage2_audit.py::check_stage2_image` checks every stage-2 image: no BSS, entry at the window
start, direct calls inside the image, no high-half load of a foreign address (stock code/data, L1, MMIO,
core MMRs, SDRAM outside the image) and the packed `main.xys2`. Data is audited by mode:

- The product's stage 2 (ABI 3): every aligned word of `.rodata` and `.data` is scanned for a foreign address.
- `--xyf-stage2` / `--xyf-stage2-menu` (model (c) of #97): their link brackets the compiler's merged strings
  (`.rodata*.str1.*`) with `__stage2_literals`/`__stage2_literals_end`, and a second link with `-q` keeps
  the data relocations (`stage2.audit.elf`; its loaded bytes must equal `stage2.bin`). Pass 1: every
  `.rodata`/`.data` relocation is `R_BFIN_BYTE4_DATA` to inside the image, outside the literal ranges.
  Literal ranges: the bracketed strings plus `STAGE2_LITERAL_SYMBOLS` (the font, exact size). Pass 2:
  the word scan over every word not wholly inside a literal range. Host fixtures: `tests/test_stage2_audit.py`.

The link map is not parsed: `ld` reports merged-string section sizes that overlap their neighbours.
Not caught (nor by the word scan): an address inside a string or the font, a `{hi, lo}` `uint16_t`
pair, unaligned words, an address built in code without a `.h =` immediate, register-call targets.

### Feature switches

The C++ is one source tree compiled with different `-DXYF_…` switches per mode.
`features(mode)` and the mode tuples near the top of `xyf_build.py` decide them.

| Switch | Modes | What it adds |
|---|---|---|
| `XYF_FILE_BOOT` | product, stage2, stage2-menu | file/library boot with the menu inside its session (`file_boot.cpp`, `xyf_boot.hpp`) |
| `XYF_MENU_LINK`, `XYF_REMOTE_READ` | product | the USB service's command link (`menu_link.cpp`), and A's storage server |
| `XYF_USB_SERVICE` | product | the USB service on B |
| `XYF_STAGE2`, `XYF_STAGE2_MENU` | stage2, stage2-menu | the stage-2 loader; ABI 2 with the menu in stage 2 |
| `XYF_MTP` | product | A's file executor and B's self-test (`mtp-test`) |
| `XYF_TFW_CHECK` | product | `tfw.check`: B reads its OTP keys (`otp_keys.cpp`) and checks a `.tfw` (`native/tfw_check`) |
| `XYF_USB_SET_DEFAULT`, `XYF_USB_FILE_WRITE`, `XYF_PLAIN_IMAGES`, `XYF_SERVICE_SCREEN`, `XYF_USB_MENU`, `XYF_MENU_ON_FAILURE`, `XYF_SERVICE_ON_MARKER_ONLY`, `XYF_REBOOT_TARGETS` | product | product boot, storage and USB features, proven on hardware |
| `XYF_ONE_SESSION` | product | boot model v3 |
| `XYF_NO_EMBEDDED` | product | no embedded OS; `xy_forever_no_image()` halt screen |
| `XYF_STATE_WRITE` | none | the menu's own set-default; parked until stage 2 |

Host tests compile `usb_service.cpp` and `storage_server.cpp` with subsets.

## Source layout

`usb_service.cpp` includes `usb_service_*.ipp` in place as one translation unit; parts are never
compiled alone. Keep each part under 32 KB with balanced braces and preprocessor guards.
`xy_forever/source_text.py::unit_text` expands them for source-reading tests. The simulator copies
and rewrites them, and the builder hashes `.ipp` and `.inc` inputs. Separate translation units
would change file-local state, define routing, link order and firmware bytes: qualify such a change
as a firmware change. For a neutral edit, every mode's candidate must match.
