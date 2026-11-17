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
  used by the library scan): the first problem refuses the whole file, with a
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
(the one rule `xyf::library::content_installed`: the content state below matches it). Such an entry is
listed ("content not installed", catalog flag 8); PLAY (boot once) and `library.boot` refuse it
(`no_content`: make it the default to install its content); SHIFT+PLAY and `library.set_default` run the
content switch first and write the default only when it succeeded (`started`, then `job.status`).

### Content state

`/yaffs2/content/` is the OS's content; a mod firmware halts without its files, and a catalog naming mods
the firmware lacks breaks the OS's menus (decision 018), so it must match the booted entry.
`content-state.txt` (read by A's library scan, head only; written by B as `.part` and renamed):

| First lines | Meaning |
|---|---|
| no file | the official content, nothing installed |
| `state=official` | the same, after a switch back |
| `state=installed`, `entry=<id>` | content/ matches assembled mod set `<id>` (only it is usable) |
| `state=switching`, `target=<id>` | a switch did not finish: nothing is usable, the menu says "content needs repair" |

Then one `file=<path>` line per file XY Forever installed (relative to `/yaffs2/content/`, any order); in an installed record (#27) each is `file=<path>` TAB
`<SHA-256 hex>`, sorted, the bytes written. The switch writes by difference: a target file whose line in a trusted record (installed, sorted, another entry than
the target) carries its SHA-256 is skipped unread; otherwise the live file is read, and written only when missing or different. Switching to the installed entry
again is the repair: it reads every file. A's engine never renames onto an existing file, so B replaces the record as record -> `content-state.old`, `.part` ->
record, `.old` removed; only a `.old` (a cut between) reads as switching, never as official. Stock entries (no `generation`) are usable only in the official
state; an assembled mod set only when the record is installed for it. A plain power-on never writes: an unusable default opens the menu with the reason
("Default <id>: content not installed" or "content needs repair").

The switch (B's job, `target/xyf_content_job.hpp`; one file request per step, USB live): the target's `info.txt` (`generation`: a mod set), the record (or its
`.old`); in the official state the live catalog's SHA-256 must equal the `op1_factory.json` line of a library entry's `content.sha256` (else refused: not the
official set), otherwise the saved list is used; the target's `content/` lines of `files.sha256` (a path the official list names is refused, as is any path the
rule refuses). Every refusal comes before the first write. Then: the official list and catalog saved under `content-official/` (official state, unless the same
copies are there); the record `switching` (the target's files and the previous ones still to remove); the previous files the target lacks removed (with folders
left empty, never an official one); the target's files written as `<path>.part`, read back, the old copy removed, renamed in; the catalog last (a stock target:
the saved official one, its SHA-256 checked); the record for the target. A failure or cut leaves `switching` (or the old or new record); running the switch
again repairs it. Writes: only the record, the saved copies and, in `/yaffs2/content/`, the catalog and paths from a mod set's `files.sha256` or the record,
never `dlc/`, `user/` or a name holding `bootloader` (`xyf::content::allowed`, one function).

### Saved configuration

`config.txt` (`target/xyf_config.hpp`): `base`, `profile`, `generation`, `unverified`, `built=<base> <generation> <set> <count>[ expanded]` (set: sum of the
names' CRC-32), `slot` (the Open-XY entry), `stamp` (its `info.txt` `sha256=`), `memory=standard|expanded` (Mod memory; anything else: standard), `mods` (last).
Replaced as `content-state.txt` is; a reader takes `config.old` when the record is missing; no record: no mods. Save builds into the spare slot unless `slot`
exists, `built` matches and its `sha256=` equals `stamp`; then the content switch (no default), the record, the default (one job, `config_store`).

`log/save-times.txt` (#27, `target/xyf_save_log.hpp`; read with `xyf_host.py save-times`): one line per
Save, USB build or content switch, the last 20; `kind result mods memory added removed total` then each
part's milliseconds and the switch's counts. Written by B in the last write window of the sequence (`.part`,
the old file removed, renamed); a failed write is ignored, a failed Save writes none.
