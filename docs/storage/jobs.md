# Content jobs and recovery

Part of [package storage](packages.md). The existing single-job exclusion covers content switches,
assembly, configuration saves, package sessions, MTP operations and storage-window closing.
`job.status` retains its protocol 1.10 layout. Reporting APIs are in [the protocol](../protocol/cmd-storage.md).

## Durable intent (`job.txt`, version 1)

Before a content switch changes shared content, B publishes `/yaffs2/xy-forever/job.txt`:
`version=1`, `target`, `previous`, `default=0|1`, `undo=0|1`, `target_info`, `target_list`,
`previous_info`, `previous_list`, then `crc32`. The four identity values are lowercase SHA-256
of the exact selected metadata and `files.sha256` bytes. A missing list is all
zeroes and allowed only for an entry without a generation. A missing or empty manifest is never an official target.
Canonical key order, LF, valid entry ids and the trailing CRC are required; the file is below 512 bytes.
A newer version or malformed record stays unchanged and the menu explains why it cannot replay it.

Publication writes `job.txt.part`, reads it back, then renames it in. Replacement moves the old
record to `job.old` first; when `job.txt` is absent, `job.old` is authoritative. A `.part` alone
is disposable and never requests recovery. A cut before initial publication leaves the old content.
The content engine keeps its existing switching record, verified copies and catalog-last order.

Only the menu/USB service loop reads and replays intent. It validates the recorded source identity,
then runs the existing content job in bounded slices. A plain power-on that finds a journal converges
first ([boot convergence](../architecture/boot/convergence.md)): the menu replays it on the progress
screen, then boots; the content-state rule still rejects an unusable default.
A missing or changed source, including a file that disappears during replay, leaves the journal and
shows `Source missing. PLAY on the first row undoes: <previous>`. PLAY on the menu's first row then
makes that previous entry the default (`content_undo`), as `library.set_default` of it does; the undo direction is published before its first
content change. A cut during replay or undo replays that direction again. Unrelated defaults cannot
replace unresolved intent. Neither `job.cancel` nor a new repair command is used for content jobs.

The journal remains through default commit and is removed only after success (`job.old`, `.part`,
then `job.txt`, so cleanup cannot resurrect older intent). The menu's converge sequence (Boot
without mods, Restore, the first row's apply; [state.md](state.md#saved-configuration)) retains intent
through its configuration/default commit: a switch is always followed by the record and the default. If
power is lost before the configuration is saved, its existing old record remains; recovery installs the
intended content/default, and the first row then reads (to apply): its PLAY converges to the record.
Both native and host collectors suspend blob deletion while any authoritative journal remains,
including a malformed or newer one. Ordinary package publication/removal retains its existing
rename transaction and disposable-scratch behavior.

## The stock entry's adoption (#178)

The first start after Track 5 wrote XY Forever onto a stock OP-XY finds no stock library entry and no default.
The converge sequence's check (`xyf_mods_save.ipp` `adopt`; at a plain power-on A doubts the missing default,
and PLAY at power-on starts it once the menu has read the library and the record, in the same case only: no stock
entry, or no record and no default) then runs the adoption, a job of B's menu loop
(`usb_service_adopt.ipp`, job kind 3: `job.status`, `busy` for file commands, no cancel). It reads the stock
updater's own `/yaffs2/upgrade/firmware/blackfin/bin/os.ldr` (never changed) and hashes it first: only the exact
stock 1.2.1 image (the size and SHA-256 `boot.uninstall` admits) goes on; a missing or other file ends it with
nothing written, and the menu says why ("No official OS 1.2.1 found." or "OS file is not official 1.2.1.", then
"Reinstall official OS 1.2.1, then start again."). In A's write window it sweeps `tmp/adopt-opxy-121/`, copies
`os.ldr` there and reads it back (SHA-256), writes `manifest.json` and `files.sha256` (as `uv run xyf-host library-add`
and a package publish) and reads them back, then renames the folder to `firmware/opxy-121`: a cut leaves only
`tmp/`, and the next start adopts again. After A's rescan it makes the entry the default; the sequence checks
again and converges (a plain power-on boots it; after PLAY the menu stays: "OS 1.2.1 is in the library and is
the default."). A stock entry there with no record and no default is only made the default ("OS 1.2.1 is the
default."). A library A could not read (no catalog, or an `opxy-121` folder its catalog does not list, as after a
failed scan) adopts nothing: "Failed: the library could not be read.". `job.status`:
phase 1 hashing, 5 copying, 6 reading back (`done`/`total` in bytes), 7 the texts, rename and default; `detail`
`adopt opxy-121` while it runs or when it failed, `opxy-121` when done; `error` 4 the file is missing, 10 not the
stock image, 8 (status `storage`) the library could not be read, 17 with status `state` an `opxy-121` entry A
lists is there already, unusable (incomplete), else 16, 18, 21 as other jobs.

## The base's content set (#183)

The adoption writes no content set, and a mod build needs its base's `content.sha256`. So before each build the
converge sequence (`xyf_mods_save.ipp` `content_set`) runs the same job in its content mode, which ends at once when
the base has one. Otherwise, reading only, it checks that the base is an official OS 1.2.1 entry and that the content
is in the official state. It lists `/yaffs2/content/` (`dlc/` and `user/` aside), sorts the paths bytewise and hashes
every file (phase 1, `done`/`total` in bytes, "Reading OS 1.2.1 content" on the menu). The list it makes must hash to
the official 1.2.1 set's digest (`official_list_sha256`, `target/usb_service.hpp`: facts, never the files). Then, in
A's write window, it writes `firmware.version` and reads it back, writes the list to `tmp/cset-<id>` and reads it back,
and renames it in as `content.sha256`, last (phase 7). Errors (status `refused`): 9 not an official OS 1.2.1 entry,
15 not the official state, 10 not the official set, 22 an `op1_factory.json` that cannot be put back (below), 7 more
than the list holds. `detail` is `content <id>`. Before that, `config.set` refuses a selection whose base has no
content set when the job could not make one: not OS 1.2.1, mods' content installed, or a catalog that is neither the
official one nor one it can put back.

An OP-XY that had Open-XY (#331): Open-XY's builder leaves its machines' presets, screens and aux presets beside the
official files and inserts their entries into `op1_factory.json` after the official ones of `engine_types`, `fx_types`
and `seq_types`; it has no uninstall. So the job reads only the official paths (`target/xyf_official_content.hpp`:
each path's SHA-256 prefix, facts) and leaves every other file where it is, outside the set. A catalog that is not the
official one is put back by keeping each of those lists' official entries; only a result with the official SHA-256
counts, else error 22 ("op1_factory.json can't be repaired"). The device's copy is then saved as
`op1_factory.replaced.json` and the official bytes replace it, both after `firmware.version`, each written under an
upload's name (`tmp/put-…`), read back and renamed over its file: a cut leaves the old file or the new one.
A boot once (`menu.converge` mode 2, `xyf dev`, #284) runs the job first too, on the base `mods.apply` would take, since
its content switch needs a stock entry's set; its error 15 there (mods' content installed) goes on to the switch, which
uses the saved official list.

## Inspection

Protocol 1.21 adds `package.list` (one item/page, inbox/library/mod store), `state.get` (paged exact
boot-slot, content and journal records), and `storage.space` (the existing 64-bit free-byte query).
All replies are at most 1 KiB. Package identity is the SHA-256 of exact `files.sha256` bytes; a
single file, including a legacy firmware entry without a list, uses the file's own SHA-256.
The image SHA/dependencies remain separate fields. Inbox file recognition uses bytes, not extensions.
Large files are hashed in bounded slices with bounded memory; callers allow the documented 600-second
package-list timeout. Legacy library boot parsing remains lenient; reporting does not change admission.
