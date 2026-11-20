# Native package readers

Part of [native JSON records](../native-json.md). Schemas and bounds:
[schema reference](schemas.md).

The native library selects `manifest.json` first and keeps legacy scanning
lenient when JSON is absent. An invalid selected JSON record makes the entry
unavailable, including as the default; scanning writes no migration files.
Only the stock raw YAFFS not-found error (-2) permits legacy fallback after an
open failure. Other open/read/close errors leave the native record unavailable.
Content switches refuse invalid native metadata before any content write.

The Mods page selects base, mod and firmware stamp metadata by the same name
precedence. Its separate directory scan checks for a dangling native name when
stat returns not found. An invalid selected native record is unavailable and the
page reports when records need a newer build. Package discovery reads metadata
for display. The mod assembly job independently checks the selected record
against its original `files.sha256` line before decoding it. Neither metadata
name enters the content file list. Strict host admission still checks every file.

The internal A-to-B catalog is XYC2 (same record sizes), with flag 16 for invalid
native metadata and a menu detail `invalid manifest.json`. XYC1 remains readable
with its original flag meanings; unknown catalog versions refuse. Package-session
and USB protocol versions do not change.

## Caller ownership and measurements

`target/xyf_package_record` extracts typed fields directly through the strict
manifest reader's shared semantic assignment and final validation. It validates
all JSON payload fields, including ignored extras, before assigning semantics;
the 4096-byte internal-view bound still applies without rendering that view.
Reports accept every supported package type. Library, content and journal source
readers require firmware metadata, even when a mod folder also contains `os.ldr`.
Legacy boot metadata remains lenient; scanning writes no migration files.

The package workspace is 11,468 bytes on Blackfin, 4096 below the earlier text
adapter. The cold scan needs 19,919 bytes including raw input/alignment, within
its existing 40,960-byte scratch reservation. The journal source owns 20,596
bytes in the content slot and hashes the exact selected metadata/list before
semantic extraction. The content job borrows its own unused list region during
metadata reading. Reports use 22,820 bytes of their existing 64 KiB slot. The
content slot occupies 26,944 aligned bytes; the content job remains 4916 bytes.
No caller borrows MTP/GC storage or allocates a JSON workspace on a stack.

Authenticated Blackfin frame measurements: direct extraction 32 bytes, JSON
schema parsing 28 and the pinned parser 20. Their deepest nested frame sum is
80 bytes, before the cold scan's unchanged 312-byte frame and outer callers.
The strict legacy manifest parser's frame is 152 bytes; native JSON no longer
calls it. Journal/content caller frames remain 132/260 bytes, the outer boot
caller 452 and the home pump 284. These are compiler frame sums, not an observed
peak stack watermark.

The product cold module is 330,784 bytes. The offline
`stage2` and `stage2-menu` modules are 53,416 and 53,756 bytes. Both fit their
56 KiB code windows and keep their 16 KiB stacks. All five modes build and
verify offline. Default package writers remain pending in #95.

The Mods reader borrows 24,012 bytes of the idle report slot while it has the
page's storage exclusion. Mod assembly needs the same amount temporarily at the
workspace's free end. It retains only each validated job input's text view, up
to 4096 bytes, for the existing linker and patch inputs. The directory scans
hold one handle and read at most one entry per step.

Blackfin frames are 324 bytes for the assembly step, 84 for the Mods native
read, 24 for the stamp reader and 284 for the directory-name scan. The
compatibility view adds an 8-byte frame before the existing JSON parser. An
equivalent synthetic build peaked at 2,972,880 bytes with native input and
2,972,848 bytes with legacy input in the stepped host harness.

The device configuration reader and encoder reuse the Mods page scratch. The
encoder validates its document against the reader's 2048-byte view limit before
starting the existing configuration job. The job holds up to 8448 serialized
bytes in its own work region and retains the existing Settings transaction.
Configuration discovery uses paged directory reads, each closed before the
menu yields. The Blackfin frames are 100 bytes for the record read, 84 for the
JSON view, 16 for the encoder and 448 for the enclosing Save step.
