# XY Forever storage layout: the mod package store

Part of the [XY Forever storage layout](../STORAGE.md). The packages themselves: [Mod packages](mods.md).

A package set lives in `/yaffs2/xy-forever/mods/<generation>/` as a flat store (protocol 1.16; `<profile>` is only in
`base/manifest.json` or legacy `base/info.txt`), so that no file is deeper than the dev-menu MTP's limit (#124, [SAFETY](../SAFETY.md)):
- `store.txt`: the store's version, `version=1` on the first line. A reader takes only its own version: no
  `store.txt` (or none it parses) is an older layout, a higher number a newer one (`mods.apply`: `not_found`
  or `unsupported` with missing 5; the Mods page skips the set, saying "need a newer XY Forever").
- `<package>/`: each top-level file (`manifest.json`, `files.sha256`, `objects.txt`, `catalog.txt`, ...) under
  its own name; every file with a folder in its path under its stored name, the first 12 hex digits of its
  SHA-256 plus its extension (the last part's text from its last `.`, not its first character, then 1-7 of
  `A-Za-z0-9`): `content/presets/acidline/corroded.json` is `<12 hex>.json`. `files.sha256` is the map;
  the job opens a listed path by it (`native/mod_job.hpp` `stored_name`; `xy_forever/modstore.py`,
  `clients/ts/src/modstore.ts`). Files with the same content share one stored file; two different files
  with one stored name are refused by the host tools. Every file is 4 folders below `/yaffs2`.
- A push writes `store.txt` into a new generation folder first and refuses a folder in another layout;
  each changed package is fully staged and verified in `tmp/`, then renamed into its place; the old
  package is detached into `tmp/` first. Python and browser pushes compare complete bytes before skipping.
  Removal detaches the package/generation before deleting it ([transactions](package-transactions.md)).

A development set (#204, `mods dev`: one mod and its base) sits in `mods/dev-<generation>/` and its `store.txt` has
a second line, `development=1`. The Mods page and `config.set` skip it when they pick the newest set (a reader that
takes only the first line sees an ordinary set); `mods.apply` builds from it by its folder name; `mods dev` removes the
older ones.

Older layouts: `mods/<profile>/<generation>/` (before 1.16: presets 8 folders deep) and the mirrored package
tree of 1.16's first builds (no `store.txt`: 7 deep). `mods list` and the web app show them as an older layout;
`mods remove <folder> --yes` deletes one (deletes work at any depth) before the set is pushed again.
