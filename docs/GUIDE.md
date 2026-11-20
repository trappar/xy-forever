# XY Forever user guide

This guide describes the current version of XY Forever (serial protocol 1.13). It is
under development and tested on one device. It is not ready for general use.

## What it is

XY Forever is a firmware and mod manager for the OP-XY. It runs when you power on,
before the OP-XY software starts. It keeps a library of firmware files on the device,
lets you pick which one starts, and lets you turn open-xy mods on and off, building
the mod firmware on the device itself.

What it never touches:

- **The real bootloader.** It stays at version 1.2.1 and is never written. Holding COM
  at power-on still opens the stock recovery menu.
- **The 1.2.5 bootloader.** It is never installed.
- **The companion chips** (keyboard and Bluetooth). They are never programmed.
- **Your projects** in `user/`. Its USB file commands refuse that folder.
- **The file `bootloader.dev`**, which keeps the stock dev menu working. Nothing in
  XY Forever can delete it.

## Getting into the menu

- **Plain power-on:** the default firmware starts, close to stock speed. Nothing is
  written to storage on a normal boot (Settings, Menu at power-on, can change that).
- **Power on holding PLAY:** the XY Forever menu opens. Let go of PLAY once you see it.
- If the default firmware is missing or fails its checks, the menu opens by itself and
  says why, for example "Default opxy-125: file missing" or "content not installed".
- With no bootable firmware at all, "No firmware found" shows one row: About.

## Keys and knobs

| Control | What it does |
|---|---|
| − / + | Move up and down. Hold to repeat (after 0.4 s, then every 0.08 s). |
| Any of the four knobs | Turn to move (clockwise moves down; Settings can reverse it). Push to select, as PLAY. |
| PLAY | Select the row. |
| STOP | Back. |
| SHIFT + PLAY | The second action of a row, where there is one (shown in the hint line). |
| TRACK 1 to 4 | Jump to the first four rows. |

The bottom line of each screen lists the keys that work there.

## The home menu

Five rows, top to bottom: the first row (Continue), Boot without mods, Mods,
Uninstall XY Forever, Settings.

### The first row (Continue)

It shows what PLAY will boot. Usually that is the default firmware's name. Once you
have used the Mods page, it reads like "Open-XY - 3 mods - 1.2.1", or the stock
firmware's name when all mods are off or in safe mode. A word in brackets may follow:

- **(changed):** you changed mod switches on the Mods page and have not saved them.
- **(rebuild):** the saved mod setup needs its firmware built again (the built copy
  is no longer in the library).
- **(to apply):** the saved setup is not what will boot yet, for example because its
  content is not installed. Save will finish the job.
- **(safe mode):** the stock firmware boots; your mods are kept (below).

With nothing pending, PLAY boots. With something pending, PLAY opens the Save dialog
(below).

### Boot without mods (safe mode)

With mods saved, PLAY asks first (Cancel or Boot). Boot puts the official content
back, makes the stock firmware the default and boots it. Your mod setup is kept. With
no mods saved the row is grey and PLAY says why.

In safe mode the row reads "Restore Open-XY (3 mods)". PLAY puts your mod firmware's
content back, makes it the default and boots it. If the saved build is no longer
intact, it builds nothing and says so: Save on the Mods page builds it again. A Save
on the Mods page also leaves safe mode.

Both stay on the home menu, with their progress on the status line. They never open
the Mods page, and they keep any unsaved mod toggles.

### Mods

This page lists the open-xy mod packages on the device. It needs packages sent from a
computer first (see `mods push` below, and [MODS-DEV.md](MODS-DEV.md)). It shows
"Reading mod packages..." while it reads them, and "No mod packages on this OP-XY."
if there are none.

The title bar shows the filter and how many of the mods it shows are on, for example
"Mods - Synths 3/57", then the package set's profile and version. Under it:

- **The memory meter:** a bar along the title bar's edge, and the line under it:
  "Memory N of M KiB, K free" ("S shared" instead: shared services, counted once). M is 2032, or 4080 with
  Mod memory at 4 MiB. Over it the bar and text turn red ("K over"). An estimate from package sizes.
- **Status (same line, on the right):** "No changes", or for example "3 mods on:
  applied when you boot", or "All off: applied when you boot". When the choice cannot
  be built it shows why, in red: "Too large: turn something off", "Turn on at least one
  machine first.", "At most 64 machines in one build.", "At most 8 extensions in one
  build.". A message (reading, a result, an error) takes the whole line until you next
  turn a mod on or off.
- **Package rows:** a box, the name, the kind and the size. PLAY (or a knob push)
  turns it on or off. Rows are sorted by kind (synths, drums, samplers, effects,
  players, extensions), then by name. A thin line separates the kinds. Nine rows show.

Keys (the bottom line names the SHIFT ones):

- **- / +** or **turning a knob:** move. SHIFT + - / +: the first / last row.
- **SHIFT + turn any knob:** the filter, one step per click: All, Synths, Drums,
  Samplers, Effects, Players, Extensions, On only (clockwise: next; it wraps). Kinds
  with no packages are skipped.
- **SHIFT + PLAY** or **SHIFT + a knob push:** turns every mod the filter shows on, or
  off if they are all on already.
- **SHIFT + STOP:** allows a base firmware XY Forever does not recognise, for the next
  save only ("Unknown mod bases allowed for the next save."); again: refused.

Turning mods on or off changes nothing on the device yet. The change is kept until
you save or discard it. STOP goes back to the home menu.

### Continue: the Save dialog

With changes pending, PLAY on the first row asks "Save changes?":

- **Save:** builds Open-XY if needed, installs its content, then boots it.
- **Discard:** forgets the changes and boots the saved setup.
- **Back:** returns to the menu; the changes stay.

When the changes cannot be saved, Save is missing and the dialog says why.

What Save does, step by step (the screen says "Saving changes" with a progress bar):

1. **Checking.** If the same mods were already built from the same packages and that
   build is still intact, it is reused and no build runs.
2. **Building.** The OP-XY assembles the mod firmware itself into a spare library
   slot ("Building: Linking", "Patching", and so on). STOP cancels this step. The last
   part, "Adding to the library", cannot be cancelled.
3. **Installing content.** The mod firmware's files are written into
   `/yaffs2/content/`. The original official files are saved first.
4. **Saving settings.** The choice is written to `config.txt`, and the default is set.
5. The new firmware boots.

During steps 3 and 4 the footer says "Almost done: it cannot be cancelled now. Keep
the OP-XY powered." With all mods off, Save skips the build and puts the official
content back.

### After a failure or a power cut

- A failed build leaves the old setup as it was. The Mods page says "Build failed:"
  and a reason.
- "Installing content failed: Continue and Save again to repair it." Do that (or
  Boot without mods, Restore Open-XY again, as it says): the same install repairs it.
- After a power cut while content was being changed, nothing boots until it is
  repaired. The menu opens and says "content needs repair". Do the same again (Save,
  or the safe-mode row). If the line says "Source missing. PLAY on the first row
  undoes: ...", PLAY on the first row puts that entry back. The repair path was tested
  in the simulator; on a real power cut it is not verified.
- "Saving the settings failed: the previous setup is kept."

### Going back to stock

- To boot stock and keep your mods for later: Boot without mods.
- To leave mods for good: turn all mods off and Save. The official content is put back.
- The last resort: hold COM at power-on to reach the stock recovery menu, and
  reinstall official 1.2.1 from there.

### Uninstall XY Forever

The row opens a confirmation with Cancel and Uninstall. In this build, choosing
Uninstall only shows "Uninstall: not available yet." The host tool's `uninstall`
command (a dry run by default) is meant for developers and is not covered here.

### Settings

PLAY opens a row. The bottom line shows saves.

- **Mod memory:** "2 MiB" or "4 MiB (experimental)", applied when you boot like the mods (Save
  rebuilds Open-XY). "Not available with these mod packages": the set has no 4 MiB layout.
- **Menu at power-on:** "PLAY held" (default) or "Always" (the menu opens at every power-on).
- **USB mode:** "MTP + Serial" (default), "MTP" or "Serial"; applies the next time the menu opens. With
  "MTP", the host tool and web app cannot reach the OP-XY until you change it back here.
- **Ask before USB control:** "On" (default): a computer's first command asks; PLAY allows it until you
  unplug or restart, STOP refuses it. MTP never asks.
- **About:** the version, the real bootloader (1.2.1, never updated), how to open the menu.
- **Content packs:** SHIFT+PLAY toggles the inbox view.

A settings file from a newer XY Forever reads as the defaults and is kept unchanged.

## The host tool: `xyf_host.py`

Python 3, standard library only. Connect the OP-XY over USB with the XY Forever
service running. Commands that change what boots are dry runs unless you add their
`--yes-…` flag. File writes are allowed only under `/yaffs2/xy-forever/` and
`/yaffs2/content/`.

| Command | Example |
|---|---|
| status | `python3 xyf_host.py status` (protocol, build and device state) |
| ls | `python3 xyf_host.py ls /yaffs2/xy-forever [--json]` |
| get | `python3 xyf_host.py get /yaffs2/xy-forever/config.txt config.txt` |
| put | `python3 xyf_host.py put notes.txt /yaffs2/xy-forever/notes.txt` (read back and compared; `--no-verify` skips that) |
| rm | `python3 xyf_host.py rm /yaffs2/xy-forever/notes.txt` (`--dir` for an empty folder) |
| mkdir | `python3 xyf_host.py mkdir /yaffs2/xy-forever/log` (the parent must exist) |
| mv | `python3 xyf_host.py mv /yaffs2/xy-forever/a.txt /yaffs2/xy-forever/b.txt` |
| cp | `python3 xyf_host.py cp -r /yaffs2/xy-forever/firmware/a /yaffs2/xy-forever/firmware/b` |
| boot | `python3 xyf_host.py boot ID --yes-boot` (boot an entry once; leaves XY Forever) |
| set-default | `python3 xyf_host.py set-default ID --yes-write` (without the flag: prints the plan) |
| mods push | `python3 xyf_host.py mods push DIR [--only ID ...] [--verify]` (send a package set) |
| mods list | `python3 xyf_host.py mods list` (package sets and packages on the device, with sizes) |
| mods remove | `python3 xyf_host.py mods remove GENERATION --yes` (without `--yes`: dry run) |
| mods apply | `python3 xyf_host.py mods apply --base ID --generation GEN MACHINE ... --wait` |
| screenshot | `python3 xyf_host.py screenshot --out screen.png` |

`mods apply` builds mod firmware on the device into library entry `mods-a` or
`mods-b`, the same build the Mods page runs. `--ext ID ...` adds extensions, `--name`
names the entry, `--memory expanded` uses the 4 MiB layout, and `mods cancel` stops a running build. It
does not install content or change the default; the Mods page's Save does both.

`--sim HOST:PORT` talks to the device simulator (`python3 -m xy_forever.sim`) instead
of the USB port. The examples above were checked against each command's `--help`, not
run on a device.

## Planned (not in this build)

- Safe mode by itself when a firmware fails at start-up.
- Choosing which base firmware a mod build uses.
- Companion chip updates (keyboard and Bluetooth), from official update files.
- MTP mode: copying files with any MTP app.
- Uninstall from the menu.
