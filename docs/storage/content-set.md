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

Open-XY leaves its machines' presets, screens and aux presets in `/yaffs2/content/` and appends its machines' entries
to `op1_factory.json`. Both modes remove them, only in the official state (no mods' content installed):

- **The light check** (`adopt_tidy`; the converge sequence's `tidy`, right after an adoption, before the stock OS
  first boots). It lists the content (`dlc/` and `user/` aside) and reads only `op1_factory.json`. Content missing an
  official path, or not in the official state, is left as it is. A catalog that is not the official one is put back
  (#331's rule) or refused with error 22 before anything is written: the menu says "Failed: op1_factory.json can't be
  repaired. Copy in OS 1.2.1's own." When there is nothing to clean (a stock OP-XY), it writes nothing. Otherwise, in
  A's write window: the catalog through an upload's name, read back and renamed over the file; the other files; the
  folders they leave empty; then `xy-forever/cleanup.txt` (`files=<n>`, `catalog=0|1`), which the install page reads.
  After PLAY the menu says "Removed 82 files from Open-XY, put back OS 1.2.1's op1_factory.json." `detail` is
  `tidy <id>`; phase 1 listing, 7 writing.
- **The content set** at the first mod build does the same for an OP-XY that had XY Forever before #334.

Only regular files whose paths are not official are removed (a link stays). A folder goes only when every entry it
had was removed, deepest first: never `content/` itself, nor a folder that was empty before.
