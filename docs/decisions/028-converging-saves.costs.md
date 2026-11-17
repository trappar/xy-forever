# 028: Saves that converge (#27, research) (costs)

Detail of [028-converging-saves.md](028-converging-saves.md).

## 3. Operations under a free-range scheme (manifest: every object's sections, addresses; the SDK sites of §1; active services, runtime set; image SHA-256)
Reference: **30 small machines** that fit standard memory (§8 list): image ~3.2 MB, objects 1.07 MB, content 0.68 MB in 138 files. A typical 30 needs expanded
memory: image ~3.9 MB, objects 1.9 MB, content 1.1 MB in 219 files [M package sizes].
| | Edits | Package bytes read | Image bytes written |
|---|---|---|---|
| Full build (today) | everything | ~6.0 MB (stock 2.37 + objects twice 2.8 + content 0.7) | 3.2 MB + 0.7 MB content, 143 files |
| Remove-one | hole (zeroed, avg 85 KB); 19 SDK sites → `kit_absent`/0; if last user: its service's objects, fallbacks, gated stock edits; runtime set; catalog, layout, lists | old image 3.2 + manifest ~50 KB + `edits.bin` 9 KB + 29 `catalog.txt` | 3.2 MB (whole copy) + 134 content files copied from the old slot |
| Add-one | first fit for its four groups (bss from the gap), else compact; its objects relocated; its 19 SDK sites; service + fallbacks + gated edits if new; runtime members; number from `numbers::layout` | old image + its package ~0.2 MB + SDK globals (manifest, or base objects 0.39 MB) | 3.2 MB + content |
| Replace-one | remove + add (same range when it fits) | as add | 3.2 MB + content |
| Compact | full build | as full | as full |
A first fit almost never succeeds right after a packed full build, because there are no holes and no gap. Add-one only helps after removes, or once full builds
leave a gap (a non-default placement).

## 4. Where a Save's time goes
Measured [M]: 1 machine job 8.4 s (0-1.0 s inputs and stock read through B's client, ~2.3 MB/s; link 1.5-2.6; patch 2.6-4.3; write 4.3-5.3; verify and finish
5.3-8.4; wall 8.9 s with the commit). 7 machines (3,086,632 B, 23 files): 11.5 s (link 1.6-4.2, patch 4.2-5.8, write 5.8-7.3, verify 7.3-11.1, renames
11.1-11.5). Phase boundaries are ±0.5 s, because the host polls every 0.5 s (`modsend.wait`). Content switch, fm2op (3 files): 0.6 s of work + 0.6-0.8 s window
close. Window close after writes: 0.3-17 s. **No Save with more than 7 mods has been timed.**
Fitted guess for 30 small [I]: stock 1.1 s; link ~1.5 s/MB of object reads → ~4 s; patch ~0.55 s/MB of image → ~2 s; write + verify + renames ~1.55 s/MB + ~0.03
s per file → ~10 s; **job ~17 s**. Unknown [U]: the commit's removal of the old 30-mod tree (~140 unlinks, one per loop pass); the window close; the content
switch, ~0.1-0.2 s per file → 14-28 s; the record. **Save ~35-70 s.**

## 5. Content files
The switch removes by difference: only previous files the target lacks (`st_remove`, `xyf_content_job.hpp:478`). But it **copies every target file**, even
unchanged ones: load, write `.part`, read back, unlink, rename (`st_copy` `:503`). `content-state.txt` keeps paths only, no hashes. Minimum for remove-one:
unlink that mod's ~6 files (and its empty folders), rewrite the catalog, and write the record twice (switching, installed). To skip a file safely, read the live
file and compare its SHA-256 with the target's line. This is read-only and avoids every change request. Do not trust the previous entry's list: the user or the
OS may have touched `content/`.

## 6. Cheaper paths that keep the image byte-identical
- **Other slot as a cache:** `check()` (`xyf_mods_io.cpp:363`) skips the build only for the slot the record names. The other slot holds the previous selection.
  Its `layout.json` (`selected`, `extensions`), `info.txt` (`generation`, `needs_os`, `sha256`) and the library's usable flag give its `built` text. If that
  equals the wanted one: no build, only the content switch and the record. One small read; this fully covers undo and toggling back.
- **Base copied on A:** saves the 2.37 MB stock read across the link (~1 s). It needs the stock write path on A, which #24 rejected (audit change). Not worth
  it.
- **Prepared per-mod pieces:** relocated bytes are valid only at one address. Pass-1 tables per mod save parsing, but pass 2 still reads every object. Small
  gain [I]. Useful only together with pinned placement.
- **Content by reference** (variant): the entry keeps `files.sha256` but no `content/` copies, and the switch reads from the package folders. This saves ~140
  writes per build and ~140 unlinks per commit. It is a STORAGE change: an entry would then depend on its packages staying.
