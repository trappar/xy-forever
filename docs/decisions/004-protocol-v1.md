# 004: Serial protocol v1 (plan item 1.1)

Research record, 2026-11-14; no device. **[C]** read from the code, **[D]** design choice, **[U]** unverified.

**Question:** what is v1 of XY Forever's USB serial protocol, and how is it specified so that other people can write tools (CLI, simulator, Web Serial app) against it?

## Decisions
1. **One JSON spec, `protocol/v1.json`** [D]. Why JSON: Python 3.9 here has no `tomllib` [C], and browsers and every language read JSON natively, so other people can use the file itself. It holds frame constants and limits, status codes (code, name, class, meaning), commands (type, name, class `core|product|dev`, `since`, build switch, request/reply fields, errors, timeout, `destructive`), events, HELLO keys and golden frames (hex). Field types: `u8 u16 u32 bytes[N] cstr text rest`. `scripts/protogen.py` (stdlib) writes `target/usb_proto_ids.hpp` (enums and `static const` only: freestanding, no BSS, no tables), `xy_forever/usb_proto_ids.py` (constants + field tables for a table-driven pack/unpack in `usb_proto.py`) and all of `docs/PROTOCOL.md`. `tests/test_protocol_spec.py` regenerates in memory and fails on any difference, and checks the golden frames with `usb_proto.encode`. TypeScript: same generator, in 6.1.
2. **Keep the frame** [C]: `'XF' | type u8 | seq u8 | length u32 LE | payload <= 4160 | CRC-32 (zlib) over all before`. Both parsers take one byte at a time, drop one byte per bad magic/length/CRC and rescan, and the device drops a partial frame after 250 ms idle, so it already suits Web Serial chunks and a TCP port for the simulator (1.3). Why: proven on the device; any change means re-testing every command.
   - Rules made explicit [D]: request types < 0x80; reply = type `| 0x80`, same seq, payload starts with u32 status [C]; one request outstanding; a tool drops replies with an old seq and resets its parser after a timeout [C: xyf_host does both]; events = device-to-host frames `0xD0..0xDF`, seq 0 (LOG `0xD0` today [C]), unknown events ignored; until HELLO every command gets `state` [C].
3. **HELLO `0x01` is frozen for all versions** [D]: empty request; reply u32 status + ASCII `key=value;...` (today `XYF1;service=usb;role=b;proto=1;install=...`, parsed by no tool [C]). v1 keys: `proto=1.N`, `build=NNN`, `mode`, `payload=4160`, `read=1024` (largest data per read reply), `stage=7340032`, `cmds=` 32 hex digits, a bitmap of the types this build answers, `dev=1` if dev commands exist. The bitmap comes from `#if` blocks in the generated header keyed by each command's build switch, and the test checks the dispatch's `#if`s match (no handler table: the builder's call audit cannot resolve indirect calls [C, `usb_proto.hpp`]). Tools ignore unknown keys.
4. **Compatibility** [D]: major 1 for all of v1. A minor bump may add commands, status codes, events, HELLO keys, and fields at the end of a reply (tools ignore extra bytes). Never within v1: renumbering, a changed meaning, a changed request layout (handlers check exact lengths [C], so new behaviour = new command). Newer tool + older device: uses only types in `cmds`, otherwise says "update XY Forever". Older tool + newer device: what it uses is unchanged. Major 2 announces `proto=2.N` in the same HELLO; a v1 tool stops. Dev commands are outside the rule.
5. **Status codes** (u32, never renumbered; a tool shows an unknown code by number and treats it as failure). From the code [C]: `ok 0`; arguments `bad_request 1`, `range 4`, `too_large 9`; `unsupported 2` (rename of `unknown`: type not in this build); `state 3` (wrong order: no HELLO, nothing staged; the same request will not succeed); device `storage 7` (A's yaffs call failed), `link 12` (A did not answer), `not_found 8`, `install 11`, `identity 13` (stock-code CRC gate); content `crc 5`, `sha 6`, `image 10`. New [D]: `refused 14` (well formed, forbidden by the safety policy: outside the write area, the brick paths; today a bad write path returns `bad_request` [C]; needed by 2.4), `busy 15` (a job runs, retry later; 4.3d), `cancelled 16` (4.3d).
6. **Transfers** [D]: stop-and-wait chunks, each with an explicit offset, as PUT_DATA and ranged READ_FILE today [C]: idempotent, resumable, and the only flow control the stock CDC path has (it recycles buffers in the interrupt and loses data when full, 002). Integrity: frame CRC, plus size, CRC-32 and SHA-256 declared at begin and checked at end (as PUT_BEGIN/END [C]); downloads check with `file.hash` (A hashes at ~4.9 MB/s, 003). Any size streams through the engine (2.1): `file.write_begin(path,size,crc,sha)`, `file.write_data(offset,bytes)`, `file.write_end`; the file appears only after end verifies; a pulled cable leaves a temporary file that the next begin or boot removes (2.1's job). RAM staging (`stage.*`, 7 MB [C]) stays only for the boot-partition install. A window > 1 only if 2.5 shows round trips dominate (HELLO key + minor bump).
7. **Jobs** [D] (4.3d install, `file.hash`): one at a time; the start command replies `ok` at once; while it runs the loop answers `hello`, `job.status`, `job.cancel`, all else `busy`; event `progress 0xD1` {job, done, total} at most 4/s, plus LOG; `job.status` {state, kind, done, total, result} survives a reconnect. Cancel acts between steps, and is refused after an install's first write (003's write order). The blocking INSTALL (900 s host timeout) and SET_DEFAULT (75 s) stay as they are [C]: proven.
8. **Dev and destructive** [D]: dev commands keep their numbers (no re-test), are class `dev`, listed in an "unstable, no compatibility" appendix and flagged by `dev=1`; new ones use `0x60..0x7F`. Destructive commands bind the payload to the target: SHA-256 of the written content (INSTALL, UNINSTALL, FILE_WRITE today [C]), path + current size for `file.delete`. Dry run by default and `--yes-*` stay host-side [C]; the spec requires tools to confirm with the user; the device refuses brick paths whatever the host says.

## Spec example
```json
{"type": "0x01", "name": "hello", "class": "core", "since": "1.0", "timeout_ms": 1000, "request": [], "reply": [{"name": "info", "type": "text"}], "errors": []},
{"type": "0x03", "name": "file.read", "class": "product", "since": "1.0", "timeout_ms": 15000, "switch": null,
 "request": [{"name": "path", "type": "cstr", "max": 240}, {"name": "offset", "type": "u32"}, {"name": "length", "type": "u32", "max": "limits.read"}],
 "reply": [{"name": "data", "type": "rest", "doc": "fewer than length bytes: end of file"}],
 "errors": ["bad_request", "not_found", "storage", "link", "refused"]}
```

## Command inventory
| Type | Today | v1 name | Mark |
|---|---|---|---|
| 0x01, 0x30, 0x31, 0x32 | HELLO, REBOOT, CONTINUE, HOLD | hello, device.reboot, menu.continue, service.hold | keep (HELLO reply extended) |
| 0x03 | READ_FILE | file.read | keep; the path-only form (<= 4 KiB) marked deprecated |
| 0x10-0x12 | PUT_BEGIN/DATA/END | stage.begin/data/end | keep, boot install only |
| 0x13 | FILE_WRITE | file.write_staged | replace by `file.write_*` (2.4), then dev |
| 0x20, 0x21, 0x40 | INSTALL, UNINSTALL, SET_DEFAULT | boot.install, boot.uninstall, library.set_default | keep, destructive |
| 0x02, 0x04, 0x05, 0x07, 0x33, 0x34, 0x60 | STATUS (build-specific structs), SCREEN, PEEK, MTP_TEST, GUARD, KEY, TEST_HANG | diag.status, .screen, .peek, .engine_test (rename in M2), .guard, .key, .hang | dev |

Planned, by item: 2.4 `file.list` (one page by index), `file.stat`, `file.write_begin/data/end`, `file.delete`, `file.mkdir`, `file.hash` (job); 4.3c `tfw.check` (pass/fail); 4.3d `job.status`, `job.cancel`, event `progress`, `package.install` (job; kinds from 4.1); 4.7 `library.remove`; 3.6 `usb.mode` (reply, then re-enumerate as MTP; the way back is 3.5's and the menu's). Interface points: each `file.*` command is one engine call (2.1 owns paths, policy, timeouts); the MTP responder (3.5) is a second client of the same calls; the web app (6.1) consumes the spec and generated client.

## Reserved for pending work
Held by unfinished work: nothing else may take these numbers, and none is on the wire yet.
- Commands: 0x1a-0x1c `file.write_begin/chunk/end` (atomic file writes); 0x47 `tfw.install` (`.tfw` install job); 0x48 `config.get`, 0x49 `config.apply` (settings over USB); 0x4a `companions.get` (companion chip versions). Removed commands stay reserved: 0x33 `diag.guard` and 0x60 `diag.hang`, if the guard is stripped.
- Statuses: 20 `over_memory`, 21 `over_machines`, 22 `no_packages`, 23 `not_official` (mod set refusals over USB). Job kind 3: `tfw.install` (0 mods.apply, 1 content switch, 2 config, 4 MTP session).

## Unproven, to measure
- Throughput of stop-and-wait with 1 KiB host chunks (`PUT_CHUNK` [C]) and 1 KiB reads; whether replies > 1 KiB still hang B (Test 038) now that `send()` writes 512-byte pieces [C, U]. Sets `read` and the chunk size (1.2c, 2.5).
- Web Serial (Chrome, macOS) with this CDC device: opens without line coding/DTR? any write pause > 250 ms inside a frame? [U]
- The loop answering `job.status` during a job (4.3d); CDC receive depth if a window is ever wanted [U]. The web app against the simulator needs the same bytes over a browser-reachable transport (WebSocket bridge): 1.3/6.1 decide [U].

## Rejected
TOML (needs 3.11 or a vendored parser) and a custom schema (a parser in every language). Generated C++ codecs (handlers stay hand-written: freestanding, call audit, candidate neutrality). A new frame (COBS, CBOR, protobuf): the current one resynchronises and is proven. Renumbering dev commands: re-test for naming only. Probing support by sending a command: unsafe for destructive ones. Windows or async replies in 1.0: nothing measured needs them. A handler table for `cmds`: indirect calls the call audit cannot check.

## Proposed split of 1.2
Later commands enter the spec with the item that builds them, one minor bump each.

| Item | Done when | Proof |
|---|---|---|
| 1.2a build: spec + generator, constants only | `protocol/v1.json` holds today's types, statuses, limits, the inventory above with today's layouts, golden frames; generated header, Python module, `docs/PROTOCOL.md`; drift test; `usb_proto.hpp/.py` take their constants from them; `scripts/neutral.py`: every candidate unchanged | host |
| 1.2b build: v1 HELLO + statuses | HELLO keys of (3), `cmds` from the switches; `unsupported` rename; `refused/busy/cancelled`; bad write path -> `refused`; xyf_host checks `proto=1.*` and `cmds`; tests pass; product builds and verifies | host + build |
| 1.2c device safe: prove 1.2b | 0.5's device pass all PASS on it (every command re-tested); round trip, read and stage throughput at 1 and 4 KiB recorded | device |
