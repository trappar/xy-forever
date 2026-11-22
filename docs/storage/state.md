# XY Forever storage layout: state files

Part of the [XY Forever storage layout](../STORAGE.md).

## Text format (`state-*.txt`, `info.txt`)

Line-based `key=value`, LF or CRLF line endings. A `#` at the start of a line
(after spaces) begins a comment. Keys are lowercase letters, digits and `_`, at
most 31 characters. Values are trimmed of surrounding spaces and tabs and are at
most 127 bytes. Files are at most 4 KiB. Two readers:

- **Lenient** (`xyf_store`: state slots, and `info.txt` in the library scan and
  the boot check): bytes are not checked, malformed lines are skipped, unknown keys
  ignored, the last of duplicate keys wins, longer names are cut to fit (47 / 31
  bytes).
- **Strict** (`xyf_manifest`: package admission and the inbox; not yet
  used by the library scan; [admission decision](package-transactions.md#reader-and-writer-decisions)): the first problem refuses the whole file, with a
  status and its line. Refused: an empty file (no key line); no LF after the last
  line (a cut copy); a line over 255 bytes; any byte outside printable ASCII
  `0x20..0x7e` (a tab only around key, `=` and value; a CR only before LF); a line
  that is not blank, a comment or `key=value`; a bad key; any key given twice; more
  than 32 key lines; a known key with an empty or invalid value. Unknown keys are
  ignored (older builds read newer files) but count and are bounded as above.

### State slots

yaffs rename is not reachable at cold time, so writes alternate between
`state-a.txt` and `state-b.txt` and are read back. Each slot ends with `seq=<n>`
and a `crc32=<8 hex>` line covering every byte before it. The valid slot with the
higher `seq` wins; the legacy `state.txt` (no CRC) is used only when neither slot
is valid (`target/xyf_state.hpp`).

| Key | Meaning |
|---|---|
| `default` | `<id>` booted when no key is held |
| `once` | `<id>` booted on the next start only, then cleared |
| `seq`, `crc32` | slot sequence number and checksum (written last) |
| `stage2`, `stage2_previous`, `stage2_tries` | planned: current/previous stage-2 file and unconfirmed start attempts |

If `default` is missing or its entry is unusable, XY Forever opens its menu.

An entry is unusable without `os.ldr`, or when its content is not installed in `/yaffs2/content/`
(the one rule `xyf::library::content_installed`: the [content state](content-state.md) matches it). Such an entry is
listed ("content not installed", catalog flag 8); `library.boot` refuses it
(`no_content`: make it the default to install its content); `library.set_default` runs the
content switch first and write the default only when it succeeded (`started`, then `job.status`).

### Content state

What `/yaffs2/content/` holds (`content-state.txt`) and how a content switch changes it:
[content state](content-state.md).

### Saved configuration

[`config.json`](native-json/schemas.md#configuration-configjson) stores the desired
selection and the built slot's identity. Readers take the first present name
among `config.json`, `config.json.old`, `config.txt`, and `config.old`. An unreadable or unsupported
selected record stops convergence and saving. Legacy records can omit `format` or use `format=1`.

A Mods page or Mod memory flip saves at a quiet moment. The USB `config.set` command saves the same
inputs. The writer stages `config.json.part`, moves any current record to `config.json.old`, then publishes
the part. A cut between renames leaves the backup authoritative. Legacy files remain readable. Reading
a legacy record and booting an already converged setup never migrate it.

Boot without mods, Restore Open-XY and Continue converge to the saved inputs
(`target/xyf_converge.hpp`, run by `xyf_mods_save.ipp`). The device builds into the spare slot when needed
and switches content if it differs. After the switch it publishes the record and commits the default via
`config_store`. Safe mode boots the base with official content and keeps the saved mods and slot.

Convergence writes `converged.txt` when its fingerprint differs. A plain power-on that finds the same
configuration bytes and default can boot immediately ([boot convergence](../architecture/boot/convergence.md)).

`log/save-times.txt` (#27, `target/xyf_save_log.hpp`; read with `uv run xyf-host save-times`): one line per
menu converge, USB build or content switch, the last 20; `kind result mods memory added removed total` then each
part's milliseconds and the switch's counts. Written by B in the last write window of the sequence (`.part`,
the old file removed, renamed); a failed write is ignored, a failed converge writes none.
