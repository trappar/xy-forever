# Files

The file methods of `Client` (`src/client.ts`; [the client](../README.md)).

Files (`test/files.spec.ts`). Paths are absolute under `/yaffs2`; changes only under `WRITE_ROOTS`
(`/yaffs2/xy-forever/`, `/yaffs2/content/`); destructive calls: show the user the path and ask first.
A change whose path fails `inWriteArea(path)` (outside the roots, an empty, `.` or `..` part, a name
holding `bootloader`) is `refused` here with the device's reason and `status: null`, nothing sent;
the device checks again.

| Method | Does |
|---|---|
| `listDir(path)` | every entry `{ name, nameBytes, kind: 'file' \| 'folder' \| 'other', size }`, all pages, device order |
| `readFile(path, { onProgress })` | the whole file in 1 KiB ranges; `onProgress(done, null)` |
| `writeFile(path, data, { onProgress })` | creates or replaces (missing folders made): stage, write, read-back CRC; 0 or above `info.limits.stage` bytes: `too_large`, nothing sent |
| `deleteFile(path)`, `removeDir(path)`, `makeDir(path)` | one file; one empty folder (else `state`); one folder (parent must exist: `not_found`; exists: `storage`) |
| `renamePath(source, target)` | renames or moves a file or a whole folder (1.8); target exists: `state`, nothing replaced; no source or target folder: `not_found`; a folder into itself: `storage` |
| `copyFile(source, target, { size })` → `{ bytes }` | copies one file on the device (1.9; at most `info.limits.stage` bytes, else `too_large`); target exists: `state`; waits 60 s + 1 s per 10000 bytes of `size` (default: from the source's listing); discards a staging |
| `copyTree(source, target, { onProgress })` → `{ files, bytes }` | as `uv run xyf-host cp -r`: a folder is `makeDir` plus one `copyFile` per file, recursively; a file is `copyFile`; a target inside the source: `RangeError`, nothing sent; stops at the first failure |
| `libraryBoot(id, { confirm: true })` | boots a library entry once and leaves XY Forever (the link drops); without `confirm: true` a `TypeError`, nothing sent |

A device error is an `XyfError` with the status as `code` (`refused`, `not_found`, `state`, `storage`, ...), the file
system's error number in `fields.error`, and a message `command path: reason` in plain words (`fileReason(error)`).
