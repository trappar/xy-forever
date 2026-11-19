# Content jobs and recovery

Part of [package storage](packages.md). The existing single-job exclusion covers content switches,
assembly, configuration saves, package sessions, MTP operations and storage-window closing.
`job.status` retains its protocol 1.10 layout. Reporting APIs are in [the protocol](../protocol/cmd-storage.md).

## Durable intent (`job.txt`, version 1)

Before a content switch changes shared content, B publishes `/yaffs2/xy-forever/job.txt`:
`version=1`, `target`, `previous`, `default=0|1`, `undo=0|1`, `target_info`, `target_list`,
`previous_info`, `previous_list`, then `crc32`. The four identity values are lowercase SHA-256
of the exact `info.txt` and `files.sha256` bytes; a missing list is all zeroes and allowed only
for an entry without a generation. A missing or empty manifest is never an official target.
Canonical key order, LF, valid entry ids and the trailing CRC are required; the file is below 512 bytes.
A newer version or malformed record stays unchanged and the menu explains why it cannot replay it.

Publication writes `job.txt.part`, reads it back, then renames it in. Replacement moves the old
record to `job.old` first; when `job.txt` is absent, `job.old` is authoritative. A `.part` alone
is disposable and never requests recovery. A cut before initial publication leaves the old content.
The content engine keeps its existing switching record, verified copies and catalog-last order.

Only the menu/USB service loop reads and replays intent. It validates the recorded source identity,
then runs the existing content job in bounded slices. A plain boot does no journal repair and adds
no journal boot gate; the existing content-state rule still rejects an unusable default.
A missing or changed source, including a file that disappears during replay, leaves the journal and
shows `Source missing. Set default to undo: <previous>`. Select that previous entry with the menu's
Set default action, or use `library.set_default`; the undo direction is published before its first
content change. A cut during replay or undo replays that direction again. Unrelated defaults cannot
replace unresolved intent. Neither `job.cancel` nor a new repair command is used for content jobs.

The journal remains through default commit and is removed only after success (`job.old`, `.part`,
then `job.txt`, so cleanup cannot resurrect older intent). Native Save retains intent through its
configuration/default commit. If power is lost before the configuration is saved, its existing old
record remains; recovery installs the intended content/default, and Save can reconcile the selection.
Both native and host collectors suspend blob deletion while any authoritative journal remains,
including a malformed or newer one. Ordinary package publication/removal retains its existing
rename transaction and disposable-scratch behavior.

## Inspection

Protocol 1.21 adds `package.list` (one item/page, inbox/library/mod store), `state.get` (paged exact
boot-slot, content and journal records), and `storage.space` (the existing 64-bit free-byte query).
All replies are at most 1 KiB. Package identity is the SHA-256 of exact `files.sha256` bytes; a
single file, including a legacy firmware entry without a list, uses the file's own SHA-256.
The image SHA/dependencies remain separate fields. Inbox file recognition uses bytes, not extensions.
Large files are hashed in bounded slices with bounded memory; callers allow the documented 600-second
package-list timeout. Legacy library boot parsing remains lenient; reporting does not change admission.
