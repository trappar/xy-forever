# 013: Web app design (plan)

Detail of [013-web-app.md](013-web-app.md).

## Additions to earlier work (each: what breaks without it)
- **Simulator:** the TCP port carries exactly the serial byte stream, for one client; `device.reboot`/`menu.continue` close the connection and listen again
  after a set delay; HELLO `build`/`cmds` can be set. Without this, reconnect and bitmap gating cannot be tested without the device. **Conformance:** cases as
  data (`protocol/conformance/*.json`), run by Python and by the TS client. Without this, two suites drift apart.
- **Generated TS client (new; build, offline, wire unchanged):** spec gaps 1-3, 5 and 6; protogen writes `clients/ts/src/v1.ts`, covered by the drift test.
  Without this, the TS client is hand-written from day one.
- **File commands:** names are UTF-8; list replies carry explicit lengths (no `rest` tail). Without this, non-ASCII names break, and `file.list` can never grow.
- **Package model, #12:** a package's identity is the SHA-256 of its `files.sha256` (for a single file, the file's own SHA-256); `package.list` reports it.
  Without this, the app must hash every file over USB to learn what the device holds.
- **#21:** (a)-(d) of the safety position. Without them, a page can switch the protection off or press the key itself.
- **Stage 2:** release builds get a manifest (version, SHA-256), plus a check that a build embeds no stock bytes before any build is hosted [U]. Without this,
  "update XY Forever" from the app has nothing to fetch.

## Split (each step one agent; the first proves the browser chain against the simulator)
| Step | Needs | Done when |
|---|---|---|
| build: generated TS + frame codec | generated TS client | `v1.ts` from protogen; encoding and parser pass the golden frames and stream vectors in Vitest (Node); drift test |
| build: call layer + Node transport | the codec, simulator, conformance | connect, HELLO, `cmds`, timeouts, events, reconnect; the conformance cases pass against the simulator over TCP, reboot included |
| build: browser end to end | the call layer | Vite page (plain DOM), Web Serial and WebSocket transports plus the relay; connect, HELLO, read `/yaffs2/firmware.version`, screenshot; automated in headless Chromium against the simulator |
| device safe: Web Serial on the OP-XY | browser end to end, v1 proven on the device | Chrome on macOS: chooser filter, HELLO, read, screenshot, reconnect after `device.reboot` without the chooser, throughput against xyf_host; DTR answered |
| #30 build: files | browser end to end, file commands | list, download (ranges, then `file.hash`), upload (`write_*` with whole-file hash), delete, mkdir, progress, cancel; simulator tests |
| #30 build: jobs + `.tfw` install | files, #14, #21 | header check in the browser, space, upload to `inbox/`, install job with progress, view restored after a reconnect (`job.status`), on-device confirmation |
| #30 build: package index | files, #12, mod packages | `xyf-index/1` schema with validators in Python and TS; fixture index; from `package.list`, the plan "send these, then apply"; exact-match dependency checks |
| #30 build: mods | package index, Mods page | send missing packages, `mods.apply`, boot once; simulator tests |
| #30 review: browser safety | jobs | fresh agent: no destructive command without a visible user action; CSP; no third-party scripts; nothing stock hosted |
