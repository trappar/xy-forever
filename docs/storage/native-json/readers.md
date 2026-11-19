# Native package readers

Part of [native JSON records](../native-json.md). Schemas and bounds:
[schema reference](schemas.md).

The native library selects `manifest.json` first and keeps legacy scanning
lenient when JSON is absent. An invalid selected JSON record makes the entry
unavailable, including as the default; scanning writes no migration files.
Content switches refuse invalid native metadata before any content write.

The internal A-to-B catalog is XYC2 (same record sizes), with flag 16 for invalid
native metadata and a menu detail `invalid manifest.json`. XYC1 remains readable
with its original flag meanings; unknown catalog versions refuse. Package-session
and USB protocol versions do not change.

## Caller ownership and measurements

`target/xyf_package_record` adds the bounded text view and semantic manifest to
the JSON workspace: 15,564 bytes on Blackfin. The cold scan needs 24,015 bytes
including raw input/alignment, within its existing 40,960-byte scratch reservation.
The journal's source reader owns 24,692 bytes in the content slot; it hashes the
exact selected metadata and list before constructing any view. The content job
borrows its own unused list region during metadata reading. Reports keep input
and workspace in 26,916 bytes of their existing 64 KiB slot. The full content
slot occupies 31,040 aligned bytes. Neither borrows the MTP or GC slot.
No JSON workspace is allocated on a firmware stack.

The package adapter's frame is 12 bytes, semantic manifest parser 164 bytes and
cold scan 312 bytes: their nested path uses 488 bytes, excluding outer boot
callers. The corresponding legacy parsing path used 476 bytes. The surrounding
cold-boot caller frames remain unchanged. Journal and content reader frames
remain 132/260 bytes, respectively, plus the nested package reader. Measurements
come from authenticated Blackfin `-fstack-usage` outputs; they are frame sums,
not an observed peak stack watermark.

The native-reader product is 314,020 bytes (module 313,948), 4,544 bytes above
the preceding `b8852f8f` product. These are partial integration measurements:
assembly/Mods readers, producers and configuration remain pending in #95.
