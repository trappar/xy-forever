# 013: Web app design (client)

Detail of [013-web-app.md](013-web-app.md).

## What `protocol/v1.json` (1.1) lacks for a generated typed client
1. Optional fields: nothing says they are all-or-none (only `usb_proto.pack`'s docstring does), so `file.read` cannot be typed as
   `{path} | {path, offset, length}`. Add the rule.
2. The statuses every command can return (`state` before HELLO, `unsupported`, later `busy`) are missing from the per-command `errors` lists. Add one top-level
   list.
3. Replies: the versions rule says tools ignore extra reply bytes, but `usb_proto.unpack` raises on them [F]. A reply that ends in `rest`/`text` can never gain
   fields. Write both down: the decoder is lenient on replies and strict on requests.
4. Encoding: `cstr`/`text` are "ASCII", but MTP clients make non-ASCII names. The file commands decide: UTF-8 bytes.
5. Transport facts are prose only. Add a `transport` object (VID/PID, product string, interface classes, line settings ignored, simulator TCP port) so tools can
   filter from the spec.
6. Test vectors cover single frames only. Add byte streams: noise, a bad CRC followed by a good frame, a frame split at every byte, an oversize length. Minor:
   `values` are prose, so the TS gets literal unions (`0 | 1 | 2`); add a per-value `symbol` only if named enums are wanted. Size-dependent timeouts
   (`file.write_staged`, install) are prose; acceptable, because long work becomes jobs, and callers may override.
Everything else is machine-readable [F]: fixed sizes, exact request sizes, `on_error`, `destructive`, `class`, `since`, `timeout_ms`, HELLO keys and the `cmds`
rule.

## Packages and the index
- **Never hosted or redistributed** [D]: official `.tfw`; any stock or decrypted `os.ldr`; firmware built from stock (open-xy builds, assembled mod sets); files
  from a `.tfw` (`content/`, `op1_factory.json`); keys. open-xy holds the same rule: "all firmware comes from the user's own device" [F README]. The user drops
  their own `.tfw`, and the device decrypts it (003).
- **Index `xyf-index/1`, static JSON** [D]. Per package: `type, id, name, version, profile, generation, needs_os`; each file's size and SHA-256; an identity
  hash; licence, notices, source repository@commit. Mods add kind, catalog id, tag, SDK minimum, and code/data/state bytes (memory meter). Official firmware is
  listed for reference only: version, `.tfw` and `os.ldr` SHA-256, content version, a link to TE's download page (010's list [F]). Dependencies stay 010's exact
  matches: "needs firmware 1.2.1" means `needs_os` equals the stock 1.2.1 `os.ldr` SHA-256; "needs base X" means the same `generation`. The device decides what
  may be installed (SAFETY's 1.2.5 rules); the index does not.
- **Hosting** [D]: the app's own origin (no CORS), files stored by SHA-256 (`packages/<sha256>`), immutable. Hosting mod packages redistributes community code,
  so each package carries its licence, notices and source pointer; kits with Timestretch are AGPL [F 008, README]. Until sharing and the licence are decided,
  the app works from files the user supplies (an open-xy kit export, package folders).
- **Fewest bytes** [D]. A `.tfw`: the browser checks the plaintext header first (magic, SKU, version, ciphertext SHA-256 [F 003]); then `storage.space`,
  `package.list`, the whole file into `inbox/` (the host cannot decrypt, so it cannot pick out members), and the `package.install` job. Sending only
  device-chosen ranges (`os.ldr` plus changed content, ~1.3 MB instead of 98 MB) waits for #6's throughput figure: a later minor bump that changes nothing
  earlier. Mods: compare `package.list` identity hashes with the index, send only the missing packages, then `mods.apply(selection)`; the device links them
  (008).

## Reuse from open-xy [F]
SDK and builder: MIT (Open-XY contributors); `ferret.mjs`: MIT (Bag of Waffles); kits with Timestretch: AGPL-3.0. `builder/core.mjs` (873 lines) holds the
reference linker (`readObject`, `linkKit`, `patchFirmware`, `recoverFirmware`, `buildAssets`, `numberLayout`, `zipFiles`). The app can use it to relink a
selection and compare it with an entry read back from the device (008's decisive check), and to recognise open-xy builds. `audition.mjs` + `sdk/browser/*` play
a kit's wasm machine previews (useful for a mod browser). `ferret.mjs` turns the dev menu on and off over Web MIDI (a recovery helper). `mtp.mjs` is WebUSB MTP
(macOS, stock OS). From `vite.config.mjs`: a localhost-only dev server with a file allowlist; CSP `connect-src 'self'`;
`Permissions-Policy: midi=(self), usb=(self)`; static `_headers` for Cloudflare. Fetching open-xy.com's kit from another origin depends on their CORS headers
[U]; linking to their site costs nothing to maintain.

**Open questions:** (1) the web app in this repository (`web/`) or in its own? (2) the confirmation default (proposed: on for code, changeable only on the
device); (3) host an index and mod packages (needs the licence decision), or start from files the user supplies? (4) Chromium desktop only: acceptable?
