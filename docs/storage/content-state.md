# XY Forever storage layout: content state

Part of the [XY Forever storage layout](../STORAGE.md). The state slots and saved configuration:
[state files](state.md).

## Content state

`/yaffs2/content/` is the OS's content; a mod firmware halts without its files, and a catalog naming mods
the firmware lacks breaks the OS's menus, so it must match the booted entry.
`content-state.txt` (read by A's library scan, head only; written by B as `.part` and renamed):

| First lines | Meaning |
|---|---|
| no file | the official content, nothing installed |
| `state=official` | the same, after a switch back |
| `state=installed`, `entry=<id>` | content/ matches assembled mod set `<id>` (only it is usable) |
| `state=switching`, `target=<id>` | a switch did not finish: nothing is usable, the menu says "content needs repair" |

Then one `file=<path>` line per file XY Forever installed (relative to `/yaffs2/content/`, any order); in an installed record (#27) each is `file=<path>` TAB
`<SHA-256 hex>`, sorted, the bytes installed. A switch compares the filesystem identities of the
live file and the prepared blob. It links an independent live copy even when it has the expected bytes.
Replaying an interrupted switch uses the same checks. A's engine renames onto an existing file only from an upload (`tmp/put-*`, #4),
so B replaces the record as record -> `content-state.old`, `.part` ->
record, `.old` removed; only a `.old` (a cut between) reads as switching, never as official. Stock entries (no `generation`) are usable only in the official
state; an assembled mod set only when the record is installed for it. A plain power-on never writes: an unusable default opens the menu with the reason
("Default <id>: content not installed" or "content needs repair"), except that a default whose content is missing
converges (#204: `menu.converge` mode 2 installed another entry's content for a boot once; [convergence](../architecture/boot/convergence.md)).

The switch (B's job, `target/xyf_content_job.hpp`; one file request per step, USB live): the target's manifest (`generation`: a mod set), the record (or its
`.old`); in the official state the live catalog's SHA-256 must equal the `op1_factory.json` line of a library entry's `content.sha256` (else refused: not the
official set), otherwise the saved list is used; the target's `content/` lines of `files.sha256` (a path the official list names is refused, as is any path the
rule refuses). Every refusal comes before the first write. Then: the official list and catalog saved under `content-official/` (official state, unless the same
copies are there); the record `switching` (the target's files and the previous ones still to remove);
remove the files the target lacks (and the empty directories they leave); link changed assets from sealed
blobs through `tmp/`; write the catalog; publish the target record. A prepared kept file and the blob alias
have the same filesystem identity. A replaced blob or older independent kept file needs a SHA-256 check
before first adoption. The live catalog remains writable for stock tools and is checked on restore. A failure or cut leaves `switching` (or the old or new record).
The menu/service replays its [durable intent](jobs.md), or offers the previous entry when its source is missing. Running the switch again also repairs it.
Writes cover the record, the saved copies, the blob store, the kept mod files and the catalog and listed paths under `/yaffs2/content/`,
never `dlc/`, `user/` or a name holding `bootloader` (`xyf::content::allowed`, one function).

