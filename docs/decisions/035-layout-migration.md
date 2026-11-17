# 035: Open-XY layouts are browser onboarding inputs

Accepted 2026-11-17, by the developer. Only the browser reads external `layout.json`
for onboarding: import the mod selection into XY Forever's native configuration,
review it against available native packages, then let XY Forever rebuild and manage
it. This is not an import of a compiled firmware image. Regular users need no local
workspace, Python or CLI. The browser flow is tracked separately in issue #94.

The hardware has no awareness of this external format. It reads native `info.txt`
and `config.txt`; assembly emits native files only. Optional `machines=<count>` in
`info.txt` is display metadata, never an editable selection. Existing entries still
boot when an unused layout file is present. Developer entry helpers also use native
metadata only; the obsolete `--layout` option is removed.

Open-XY's `selected` and `extensions` describe a build's selection; `machines` is a
history of assigned numbers. A layout without `selected` cannot recover the selected
mods and must never fabricate them from that history. Browser onboarding will validate
profile/memory and package compatibility, show missing IDs, and require review before
native configuration is changed. It will not send the external document to the device.

This supersedes the device layout-reader and layout-output proposals in decisions
005, 008, 010, 014 and 019. Keeping Open-XY's format on the device would couple native
runtime storage to an external builder for information the native selection already owns.
