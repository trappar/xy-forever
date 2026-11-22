# Mod packages and builds

Mod packages and the builds on the device (`src/mods.ts`, `src/config.ts`; [the client](../README.md)).

Mod packages (`src/mods.ts`, `test/mods.spec.ts`), as `uv run xyf-host mods push | list | remove`: the host
only puts packages on the OP-XY (the device assembles mod firmware itself). A package set is one
generation folder from `modkit split`; it goes to `MODS_ROOT/<generation>/<package>/<stored name>`, the flat
store (`src/modstore.ts`, docs/storage/mods.md: `store.txt`, a file with a folder in its path under its
SHA-256's first 12 hex digits and extension; `MODS_ROOT` = `/yaffs2/xy-forever/mods`; generation from
`base/info.txt`; protocol 1.16, older devices are refused with `update_required`; a folder without `store.txt`
lists with `older: true`, a newer store with `newer: true`). Every change keeps the depth rule (`src/depth.ts`,
#124): at most 6 folders below `/yaffs2`. A device or
package-set problem: `XyfError` or `PackageError`.

| Function | Does |
|---|---|
| `pushPackages(client, source, { only, verify, onPackage, onProgress })` → `{ sent, skipped, files, bytes, seconds, packages, verified }` | checks every file against its package's `files.sha256` first (nothing sent on a bad set); per package (base first): the listed files, then `info.txt`, then `files.sha256` last; a file the device holds with the same size is skipped; if anything in a package goes over, the device's `files.sha256` is deleted first, so a folder holding it has every listed file. Stops at the first failure; pushing again sends exactly the rest. `only`: these packages plus base; `verify`: reads everything back. `onProgress(done, total, 'send' \| 'verify')` in bytes |
| `listPackages(client)` → `[{ profile, generation, older, newer, packages: [{ name, info, files, bytes, complete }] }]` | what the device holds; `complete: false`: an interrupted push (push again) |
| `removePackage(client, what, { generation, dryRun })` → `[{ path, files, bytes }]` | a generation folder (`what` names a generation) or that package in every generation (or in `generation`); `files.sha256` first, then the files and folders; `dryRun`: what would go (for the confirmation); `[]`: nothing named `what`. Ask first |
| `fileListSource(files)` / `folderSource(dir)` (`./node`) | the `source`: a folder chosen with `<input type="file" webkitdirectory>` (the generation folder), or a folder on disk |
| `localSet(source, only)` | the checked package set, in send order, without the device |

Building on the device (`test/mods.spec.ts`), as `uv run xyf-host mods apply --wait | cancel`: the OP-XY assembles
the firmware itself from the packages it holds; the host only starts, watches and cancels the job. The job works in
`jobFolder(id)` = `/yaffs2/xy-forever/tmp/job-<id>/` and commits the result as library entry `mods-a` or `mods-b`,
listed with its content not installed (it does not boot until `makeDefault` installs its content). `applyMods` and
`makeDefault` are developer tools: they change what boots behind the saved inputs until the next plain power-on.

| Function | Does |
|---|---|
| `configGet(client)` → `{ saved, mods, expanded, unverified, write, base, generation }` | the saved inputs (`config.get`, protocol 1.24): what the next boot of XY Forever converges to |
| `configSet(client, { mods, expanded, unverified }, { readMs, pollMs })` → as `configGet` | destructive (ask first): `config.set`, the product way to change mods, as the device's Mods page does (safe mode stays as saved: a recovery action on the device only): sent again while the device reads its package list (`busy`), then followed until written; a selection the device cannot build is an `XyfError` `refused` whose message is the device's reason. Builds nothing: the next boot does |
| `applyMods(client, { base, profile, generation, machines, extensions, name, unverified }, { onProgress, signal, pollMs, stallMs })` → `{ entry, state, status }` | destructive (ask first): `mods.apply`, then `job.status` every `pollMs` (500) until the job ends; `onProgress(status)` each time; resolves with the library entry id (`mods-a` or `mods-b`, the final status's `detail`), `state: 'content not installed'` and the final status (`elapsedMs`, `steps`). `unverified`: the user confirmed a base that is not a known stock firmware. A refusal: `XyfError` with the device's code and a plain reason (`busy`; `not_found` with `fields.missing`, `MISSING_WORDS`); a failed or cancelled job: `JobError` (`status`, the reason in words); aborting `signal` sends `job.cancel` and waits for the clean-up; no step for `stallMs` (90 s): `XyfError` `timeout` (the job keeps running) |
| `makeDefault(client, id, { onProgress, pollMs, stallMs })` → `{ id, switched, status }` | destructive (ask first): `library.set_default`; an entry whose content is not installed answers `started` and the content switch runs as a job, followed through `job.status` until the default is written (`switched: true`); a failed switch: `JobError` (`what: 'content switch'`, the default unchanged) |
| `jobStatus(client, job = 0)` → `JobStatus` | `{ job, phase, done, total, error, code, elapsedMs, steps, longestMs, peak, debris, detail, final, folder }`; job 0: the running or last one; when done, `detail` is the library entry id; `folder`: its work folder in `tmp/` (debris of a failed job) |
| `cancelJob(client, job = 0)` → `{ debris }` | stops a build; answers once its files are removed; one that ended, one in its library commit, or a content switch: `state` |
| `libraryBases(client)` → `[{ id, name, contentList }]` | firmware library entries holding an `os.ldr` that are not assembled mod sets (candidates for `base`; `mods-a`/`mods-b` left out); `contentList`: it has `content.sha256`, which a build needs |

While a job runs, every command that needs storage answers `busy`: `isXyfError(error, 'busy')`, with the reason
in the message; wait for the job (or cancel it) and try again.
