# 004: Serial protocol v1 (detail)

Detail of [004-protocol-v1.md](004-protocol-v1.md).

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
| 0x13 | FILE_WRITE | file.write_staged | replace by `file.write_*`, then dev |
| 0x20, 0x21, 0x40 | INSTALL, UNINSTALL, SET_DEFAULT | boot.install, boot.uninstall, library.set_default | keep, destructive |
| 0x02, 0x04, 0x05, 0x07, 0x33, 0x34, 0x60 | STATUS (build-specific structs), SCREEN, PEEK, MTP_TEST, GUARD, KEY, TEST_HANG | diag.status, .screen, .peek, .engine_test (rename in M2), .guard, .key, .hang | dev |

Planned: `file.list` (one page by index), `file.stat`, `file.write_begin/data/end`, `file.delete`, `file.mkdir`, `file.hash` (job); `tfw.check` (pass/fail); #14
`job.status`, `job.cancel`, event `progress`, `package.install` (job; kinds from decision 010); `library.remove` (stage 2); `usb.mode` (reply, then re-enumerate
as MTP; the way back is the MTP responder's and the menu's). Interface points: each `file.*` command is one engine call (the engine owns paths, policy,
timeouts); the MTP responder is a second client of the same calls; the web app consumes the spec and generated client.

## Reserved for pending work
Held by unfinished work: nothing else may take these numbers, and none is on the wire yet.
- Commands: 0x1a-0x1c `file.write_begin/chunk/end` (atomic file writes); 0x47 `tfw.install` (`.tfw` install job); 0x48 `config.get`, 0x49 `config.apply`
  (settings over USB); 0x4a `companions.get` (companion chip versions). Removed commands stay reserved: 0x33 `diag.guard` and 0x60 `diag.hang`, if the guard is
  stripped.
- Statuses: 20 `over_memory`, 21 `over_machines`, 22 `no_packages`, 23 `not_official` (mod set refusals over USB). Job kind 3: `tfw.install` (0 mods.apply, 1
  content switch, 2 config, 4 MTP session).

## Proposed split
Later commands enter the spec with the item that builds them, one minor bump each.

| Step | Done when | Proof |
|---|---|---|
| build: spec + generator, constants only | `protocol/v1.json` holds today's types, statuses, limits, the inventory above with today's layouts, golden frames; generated header, Python module, `docs/PROTOCOL.md`; drift test; `usb_proto.hpp/.py` take their constants from them; `scripts/neutral.py`: every candidate unchanged | host |
| build: v1 HELLO + statuses | HELLO keys of (3), `cmds` from the switches; `unsupported` rename; `refused/busy/cancelled`; bad write path -> `refused`; xyf_host checks `proto=1.*` and `cmds`; tests pass; product builds and verifies | host + build |
| device safe: prove the v1 HELLO build | the device pass all PASS on it (every command re-tested); round trip, read and stage throughput at 1 and 4 KiB recorded | device |
