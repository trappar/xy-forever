# XY Forever storage layout

Everything XY Forever owns on the OP-XY lives under `/yaffs2/xy-forever/` (processor A's yaffs volume): boot state, the firmware library, mod packages,
reports and the saved configuration. These pages give each file's format and the bounds its readers enforce.

## Contents

- [The folder](storage/layout.md): the `/yaffs2/xy-forever/` tree, what changes by hand may touch, the reporter's `log/`, entry ids
- [State files](storage/state.md): the `key=value` text format, the A/B state slots, the content state and its switch, the saved configuration
- [Content jobs](storage/jobs.md): durable intent, automatic menu/service replay, missing-source undo and inspection
- [Package transactions](storage/package-transactions.md): atomic publication/removal, verified content blobs and collection
- [Package files](storage/packages.md): `info.txt` keys, `files.sha256`, recognising a dropped file or folder, stage 2 (`stage2/main.xys2`)
- [Native JSON schemas](storage/native-json.md): independently versioned package/configuration records and bounded adapters (storage integration in #95)
- [Mod packages](storage/mods.md): browser-only Open-XY migration boundary; mod packages: base, machine, extension, `edits.bin`, the linker and its bounds
- [The mod package store](storage/mods-store.md): a package set on the device, flat (`store.txt`, stored names), older layouts
- [Mod assembly](storage/mods-assembly.md): assembly after the link, the job on the device, the assembled entry, into the library, trust
