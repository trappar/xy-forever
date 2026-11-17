# 010: Package model (plan)

Detail of [010-package-model.md](010-package-model.md).

## Dependencies, host view, space
- **Dependencies.** Device, each because a mistake there is made on the device with no host present [D]: (1) `mod.generation == base.generation` and Apply only
  with an entry whose `sha256` equals the base's `needs_os` (else the assembled OS crashes or cannot be built); (2) a content switch only when every source
  exists; (3) refused types (`companion`; `bootloader` members, `package_policy::classify` [C]); (4) `XYS2` `min_stage1` [C]; (5) remove lists dependents
  (`needs_os`) and asks; it does not refuse (recoverable, user rule). Host and web app: which versions exist, compatibility (profile, SDK minimum), updates,
  downloads, licences. The device never compares versions.
- **Host view.** Queries: `package.list` (inbox items with recognised type, entries, mod cache: id, type, version, sha256, needs), `state.get` (slots, content
  record, journal), `storage.space` (statfs, A proven [C 009]), `job.status`. Commands (jobs): `package.install`, `package.remove`, `library.set_default`
  (becomes activate), `mods.apply`, `content.repair`, `job.cancel`; files through `file.*`. Index for the web app needs per package: type, id, name, version,
  file sizes and SHA-256, `needs_os`/`generation`/`profile`, source and licence; `.tfw`: firmware and content versions, SKU; mods: machine kind, catalog id,
  tag/commit, SDK minimum, cpu and memory size (Apply's 2032 KiB meter).
- **Space.** Measured [C]: `.tfw` ~97.8 MB; `os.ldr` 2.30-2.37 MB; content 125.8 MB / 839 files; 1.1.33 vs 1.2.1 content ~100 KB; whole mod catalog ~8.7 MB;
  assembled `os.ldr` <= 2.37 MB + 2.03 MB module. Unknown [U]: yaffs size and free space (#17), the stock `upgrade/` leftover, write speed. Kept: entries,
  blobs, mod cache, two assembled sets, two stage 2 files. Deleted: the `.tfw` after install, `tmp/`, unreferenced blobs. Rule: a job adds its worst case
  (staging + result + blobs) and refuses before the first write if free space minus a reserve is short (reserve size: set after #17) [D].

## Build items and plan changes
- **build: manifest + recogniser** | needs the v2 flow gone | `info.txt` keys, streamed `files.sha256`, inbox classifier, accept checks; Python twin in
  `xyf_entry.py`; host tests per kind, legacy `info.txt` and malformed input; every candidate unchanged.
- **#11 build: commit, remove, blobs** | the manifest, #4 | `tmp/` build + rename commit, remove-by-rename, sweep, blob collection; simulator: a cut between
  every two steps leaves the library valid.
- **#12 build: job runner + journal + repair** | #11, the v1 HELLO build | `job.txt`, repair at menu/USB start, `job.status`/progress, `package.list`,
  `state.get`, `storage.space`; simulator: a cut at every step finishes or offers undo.
- Then: #14 (install, adds blobs) needs #12; #18 becomes the content switch and absorbs the earlier content-switch step; content per entry shrinks to its device
  question (absent machines in `op1_factory.json`, decision 018); #24 needs #12; stage 2 needs #11; #21 needs only the manifest and #4 (not #14); the content
  manifests write `content.sha256`; the host import writes the new `info.txt`.
- **Open questions:** (1) "Try once" for an entry with other content: a power cycle cannot return to the old default without a write at boot; Undo from the menu
  instead? (2) delete each `.tfw` after install (proposed) or keep? (3) delete stock's `upgrade/` leftover? (4) leave unknown files in `content/` (proposed) or
  remove them as stock does?
