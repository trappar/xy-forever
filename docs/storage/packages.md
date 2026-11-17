# XY Forever storage layout: package files

Part of the [XY Forever storage layout](../STORAGE.md). Its files use the [text format](state.md#text-format-state-txt-infotxt).

## `info.txt` (package manifest)

Every package folder's manifest (decisions/010). Without `type` it is a firmware
entry: every `info.txt` written before `type` existed is one, unchanged.

| Key | Value (strict reader) |
|---|---|
| `type` | `firmware`, `mod-base`, `mod`; `companion` is read only to be refused; any other value is refused (`.xys2` and content packs have their own manifests: the `XYS2` header, the pack's `manifest.json`) |
| `name` | display name, at most 47 bytes, such as `OP-XY 1.2.5` |
| `version` | version text (display and equality only), at most 31 bytes |
| `kind` | `official`, `open-xy` or `custom` |
| `bytes` | size of `os.ldr`: decimal, no sign, no leading zero, below 2^32 |
| `machines` | optional decimal count, 0..65535: selected machines for firmware, catalog machines for a mod-base; absent means unknown |
| `crc32` | CRC-32 of `os.ldr`, 8 lowercase hex digits (checked before boot) |
| `sha256`, `needs_os` | 64 lowercase hex digits: the `os.ldr` SHA-256; the one this package needs |
| `content` | the content set's version (`content.sha256` holds the set), at most 31 bytes |
| `generation`, `profile` | kit export and OS profile (`opxy-1.2.1`): `[a-z0-9][a-z0-9._-]*`, at most 31 bytes |
| `machine` | `synth`, `drum`, `sample`, `effect`, `player` or `extension` |
| `source`, `licence` | `<repository>@<commit>` (127 bytes) and licence (63 bytes) |

| `type` | Required | Also allowed |
|---|---|---|
| none | nothing | the firmware keys |
| `firmware` | `name bytes crc32 sha256` | `version kind content source licence profile machines`, `generation` + `needs_os` (together: an assembled mod set) |
| `mod-base` | `profile generation needs_os` | `name version source licence machines` |
| `mod` | `name machine generation` | `version profile source licence` |

Any other known key for a type is refused. Dependencies are exact matches, checked
on the device: a base or an assembled set runs only on the entry whose `sha256` is
its `needs_os`; a mod's `generation` (and `profile`, if given) equals its base's; an
assembled set's `generation`, `needs_os` and `profile` equal its base's. Host twin:
`xy_forever/xyf_entry.py` (`parse_manifest`, `manifest_text`); `python3 -m
xy_forever library-entry` builds an entry; `xyf_host.py library-add` sends one.

## `files.sha256`

What a multi-file package owns, in `shasum -a 256` format: one line per file,
`<64 lowercase hex><space><space or *><path>` and LF (the last line too). Paths are
relative to the package folder, at most 160 bytes of printable ASCII without `\`
or `:`; no empty, `.` or `..` name, no name ending in `.` or space; never the list
itself or a name containing `bootloader`. Lines are sorted by path, bytewise
(`LC_ALL=C sort`), so a duplicate is always the line before: the device streams the
list with one line of memory, at most 4096 entries. Host: `files_sha256_text`.

`content.sha256` (a firmware entry's content set) is the same form, with paths relative to
`/yaffs2/content/`. `xyf_package.py content-manifest CONTENT --out DIR` writes it from an
official package's `firmware/blackfin/content/` with `content.sizes` (host only, `<bytes>  <path>`)
and `firmware.version`; `content-compare A B` (lists or folders) exits 0 when identical.

## Recognising a dropped file or folder (`xyf_manifest` recogniser)

Files, by their first 268 bytes and size, never by extension:

| Kind | Rule |
|---|---|
| official `.tfw` | `babecafe` + `beefcafe` at 0x40 (or a bare `beefcafe`), `TESG` at +0x84 of the crypto header, format words 1, 1 |
| LDR image | first block: XOR of its 16 bytes 0, byte 3 `0xad`, flags `0x5004`, count 0, length word = size - 16 (custom firmware or an XY Forever stage 1: not told apart) |
| stage 2 | `XYS2`, at least 96 bytes (`xyf_stage2` checks the rest) |

Stage 2 (`target/xyf_stage2.hpp` ABI 3): `stage2/main.xys2` is read by B's USB service at the first HELLO of a boot (never on a plain boot), checked (header,
CRC, SHA-256, window `0x8ab90000..0x8ac00000` (since 171), `min_stage1` <= the stage-1 build) and copied to its load address; stage 2 makes no stock call and
reaches stage 1 only through the append-only services table. Any failure leaves stage 1 as before (STATUS `usb_service.stage2` says why). Built by `xyf_build.py
--xyf-product` as `main.xys2` beside `candidate.ldr`.
| ZIP | `PK\3\4` or `PK\5\6`: an open-xy builder ZIP, imported by the host only |

Folders, by their names and `info.txt`: `info.txt` decides by `type` (firmware
entries need `os.ldr`; one with `generation` is an assembled mod set); without it, `manifest.json` is a content pack and `os.ldr` a
firmware entry. `content.sha256` and `firmware.version` (a content set) come
together or not at all. Refused always: a name that is not one safe name (1-127
printable ASCII bytes, no `/`, `\` or `:`, not ending in `.` or space), any name
containing `bootloader` (case ignored), the companion images `*.cyacd` and
`ble.bin`, and `type=companion`. Anything else is unknown and refused.
