# XY Forever storage layout: package transactions

Part of [Package files](packages.md). Packages are admitted strictly; reading a library at boot stays lenient.

## Publication and removal

`library-add`, `mods push` and the browser's package push build the complete package under
`tmp/pkg-v1-<token>/`, check its manifest and sorted file list, verify every file, then publish by one
directory rename. A replacement first renames the old folder to `tmp/pkg-v1-<token>-old`; a removal
renames the package into `tmp/` before deleting its tree. After a cut the target is old, new or absent,
never a partially admitted package. An absent default opens the menu. State slots are unchanged.
The native assembly job uses its existing `tmp/job-<id>/` and the same rename-away/rename-in order.
Standalone stage-2 files use the existing verified `tmp/put-*` upload and atomic file rename.

A plain sound pack is already staged in `xy-forever/inbox/<id>/`. Install checks its manifest, every
folder and the final path, then renames it to the unoccupied `content/dlc/<id>/`. Both pack roots have
the same depth. Removal checks the same tree, renames it into `xy-forever/tmp/pkg-v1-*/`, then cleans up.
A cut before the rename keeps the whole source in place. A cut after removal leaves only disposable scratch.
The existing startup sweep takes at most 600 steps per pass, so a large interrupted cleanup can span several passes.
A serial change takes the package session; a menu change holds the storage job exclusion throughout.

Host transactions use `package.session` (protocol 1.19, session ABI 1): an owner token excludes the
startup sweep, MTP, other jobs and boots while the ordinary file RPCs stage/check/commit. It adds no
storage operation. A contender cannot renew another owner's token; busy acquisition requests cannot
keep an abandoned owner alive. Owner renewals and file activity renew the ten-second idle period;
an in-flight upload or package-list read keeps it held. Release closes the write window asynchronously. A newer session
ABI is refused. This is cooperative exclusion for one CDC client, not a security boundary on manual
file operations. The marker, updater inbox, boot-partition latch and depth rules still apply on A.

When no job runs, the existing bounded startup sweep removes disposable `tmp/job-*`, `tmp/pkg-*`
folders and abandoned `tmp/put-*` uploads. It also runs after an abandoned/released package session.
The collector below follows after eight seconds without host traffic. It re-enters A's file service
after a window close/catalog refresh, and yields at completed IO boundaries to foreground work. Library and mod transactions sweep versioned scratch before
starting. Sound pack changes leave other scratch to the idle sweep.

## Content retention and collection

`blobs/<64 lowercase SHA-256 hex>` contains the raw, complete bytes named by the hash; there is no
separate disk index or new serialized container. Firmware references come from every kept entry's
`content.sha256` and the `content/` lines of its `files.sha256`. A shared hash has one stored copy.
The store may also retain a live file; collection depends on kept references, not current installation.

Host `library-add --content` hashes existing live factory files and uses a matching file directly.
The saved official catalog can satisfy its own reference while mods are installed. A matching live
file replaces a redundant legacy cache copy when no mod entry or recorded pack needs its blob name.
Other hashes use the existing cache entry or a verified upload from the supplied files.

Native assembly and first adoption bind each kept asset to its sealed blob. A missing blob is copied
through scratch and verified. A writable legacy blob is sealed in 4 KiB pieces and checked by SHA-256
before replacement. For an existing sealed blob whose filesystem identity differs from the kept file,
the staged alias is verified before the kept name is replaced. Prepared assets use identity checks
and links during switching ([content state](content-state.md)).

`.tfw` sound packs use the same verified blobs. The importer decrypts it, checks the destination
and space, and extracts into a private folder. B writes each file into a new
read-only file A creates (`managed_blob_link` create, put), hashing what it sends: no copy, no read-back.
One rename makes it the blob (an existing blob is read back, then linked), and the tree links to it.
The records live in `xy-forever/pack-records/ID/`: `xyf-pack.txt` with the header's (unchecked) ciphertext digest and
`xyf-pack-v1.sha256` with each file’s SHA-256 and relative path, published by one rename before the tree's.
The tree is retained under `xy-forever/packs/` and moves to or from `content/dlc/` when toggled; the records stay
and give its blobs' reachability.

Host changes collect in-session. Native publication schedules idle collection. An unresolved
`job.txt` or `job.old` suspends all blob deletion until [replay or undo](jobs.md) completes. It holds the job
exclusion while collecting all kept references, then deletes only unreferenced hash names. The native
collector streams each list once, reuses 4 MiB of completed-job workspace for at most 131,072 hashes
(16 entries × two 4,096-line lists), sorts in bounded heap steps and binary-searches blob names.
It leaves the job's status and MTP's reserved slot intact. A malformed, newer or oversized list/index
stops collection before deletion. A cut during deletion can leave extra blobs, never remove a kept
reference; a later idle collection resumes by deriving reachability again.

`library-remove ID --yes-write` detaches an entry and collects; `library-gc --yes-write` collects alone.
`scripts/package_pass.py --sim HOST:PORT,CONTROL --base ID --yes-write` proves the concrete storage test before
the same script is used over USB: two fresh entries, shared/last-reference GC, unchanged state slots,
then removal of its scratch entries. It never boots, resets or writes live content.

## Reader and writer decisions

The library scan and boot check retain `xyf_store::parse_info`, including legacy manifests and lenient
handling of unknown keys, malformed lines and long display text. Adopting the manifest model in that
scan must preserve those boot semantics; admission is where strictness belongs. New host/browser
packages use the strict manifest/list rules, with the existing native readers as the reference.

The restrictions in [Package files](packages.md) are retained individually:

- Required fields establish file integrity and exact OS/base/generation dependencies; accepting an
  incomplete new package would defer an admission error until assembly or boot.
- Fixed type/kind/machine words select supported handlers, catalog wire enums, official-base lookup
  and assembly roles. Display-only name/version/source/licence remain free text within their bounds.
- Generation/profile alphabets keep path-derived identifiers and exact dependency matches canonical.
- Unsupported types, especially companion images, have no package handler here and are refused;
  admitting a type does not grant permission to program companions or the bootloader.

New packages write `manifest.json`. The Python `package_records.document`
writer and the bounded assembled-entry writer in `native/mod_job_text.ipp`
use the same field order and encoding. File lists are sorted bytewise, hash the
exact emitted bytes and use the same 160-byte relative-path limit.
