# The base's content set and Open-XY's leftovers

Part of [content jobs](jobs.md). The adoption's job (`usb_service_adopt.ipp`, job kind 3) in its two other modes.

## The base's content set (#183)

The adoption writes no content set, and a mod build needs its base's `content.sha256`. So before each build the
converge sequence (`xyf_mods_save.ipp` `content_set`) runs the same job in its content mode, which ends at once when
the base has one. Otherwise, reading only, it checks that the base is an official OS 1.2.1 entry and that the content
is in the official state. It lists `/yaffs2/content/` (`dlc/` and `user/` aside), sorts the paths bytewise and hashes
every file (phase 1, `done`/`total` in bytes, "Reading OS 1.2.1 content" on the menu). The list it makes must hash to
the official 1.2.1 set's digest (`official_list_sha256`, `target/usb_service.hpp`: facts, never the files). Then, in
A's write window, it writes `firmware.version` and reads it back, writes the list to `tmp/cset-<id>` and reads it back,
and renames it in as `content.sha256`, last (phase 7). Errors (status `refused`): 9 not an official OS 1.2.1 entry,
15 not the official state, 10 not the official set, 22 a catalog not put back (below), 7 more than the
list holds. `detail` is `content <id>`. Before that, `config.set` refuses a selection whose base has no
content set when the job could not make one: not OS 1.2.1, mods' content installed, or a catalog that is neither the
official one nor one it puts back.

After Open-XY (#331, `usb_service_adopt.ipp`): only the official paths are read (`xyf_official_content.hpp`);
another catalog is put back by keeping each list's official entries, only at the official SHA-256 (else 22). The
other files are removed (#334, below), after the catalog and before the list.
A boot once (`menu.converge` mode 2, `xyf dev`, #284) runs the job first too, on the base `mods.apply` would take, since
its content switch needs a stock entry's set; its error 15 there (mods' content installed) goes on to the switch, which
uses the saved official list.

## Open-XY's leftovers (#334)

Open-XY leaves its machines' presets, screens and aux presets in `/yaffs2/content/` and appends entries to
`op1_factory.json`. Cleanup runs when the live content is in the official state.

- **First start and upgrade.** The light check (`adopt_tidy`) runs after adoption. An older install that lacks
  `xy-forever/cleanup.txt` gets the same check at its next plain or PLAY power-on with official content installed.
  It lists `content/`, skips `dlc/` and `user/`, and reads only the catalog. A missing official path leaves the
  content alone. If the catalog differs, removing the Open-XY entries must produce the official SHA-256. Otherwise
  the job stops before writing and reports error 22, "Failed: op1_factory.json can't be repaired. Copy in OS 1.2.1's own."
- **Uninstall.** After the switch back to official content, `adopt_uninstall_tidy` checks again even if a completion
  record exists. It refuses an unfinished switch, installed mod content, or a missing official path.
- **First mod build.** The content-set job applies the same file rules before saving the verified official list.

In A's write window, the job restores a changed catalog through a temporary file, reads it back and renames it
over the live catalog. Then it removes non-official regular files. Links stay. A folder goes only when every
entry it had was removed, deepest first. The content root and previously empty folders stay.

First-start and upgrade checks publish `xy-forever/cleanup.txt` last by rename. It records `files=<n>` and
`catalog=0|1` for the install page, including zero counts when there was nothing to remove. An interrupted
check retries at next start. After PLAY, the menu reports changes such as "Removed 82 files from Open-XY,
put back OS 1.2.1's op1_factory.json." The job detail is `tidy <id>`, with phase 1 for listing and 7 for writing.
