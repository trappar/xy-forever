# 013: Web app design
Research record, 2026-11-14, at open-xy `4718876`; no device, no network. **[F]** read from files, **[D]** design choice, **[I]** inferred, **[U]** unverified:
browser facts are general knowledge as of mid-2026 and must be re-checked against current Chrome, MDN and WICG docs before the call layer is built.
**Question:** what the web app (a package manager that is a convenience, never a requirement) needs from the rest of the project, what earlier items must add,
and how its build splits.

## Decisions
1. **Web Serial on the CDC port is the app's only device door** [D]: the serial protocol covers everything, and MTP stays for ordinary file tools. WebUSB adds
   nothing: the OS's CDC driver holds the interface on macOS, Windows and Linux [U]; open-xy's WebUSB MTP works only on macOS, against the stock OS's
   vendor-class MTP [F `builder/mtp.mjs`].
2. **Chromium desktop only (Chrome, Edge, Opera)** [D]: other browsers are told to use MTP and the menu. There is no fallback transport, because the app is
   optional.
3. **The TS client lives here (`clients/ts/`); `scripts/protogen.py` writes its spec part (`v1.ts`) and the Python drift test checks it** [D]: the spec, the
   generator and the client change in one commit, and the check needs no Node. Where the web app lives is open question 1.
4. **Generated or hand-written** [D]. Generated: constants; status codes, names and classes; the command table (type, name, class, since, destructive, timeout);
   field tables; request/reply types; one typed encode/decode per command; events; HELLO keys; golden frames as test data. Hand-written (~600 lines): frame
   encoding and the resync parser (a fixed algorithm, hand-written in `usb_proto.py` too: generating it buys nothing), HELLO parsing and the `cmds` check
   (BigInt), transports, the call layer (one outstanding request, seq, stale replies, timeouts, parser reset, events), connect and reconnect, jobs and
   `progress`, chunked transfers, CRC-32, and SHA-256 by `crypto.subtle.digest` over the whole file (WebCrypto cannot hash a stream [U]; 98 MB in memory is fine
   on a desktop). `dev`/`test_only` commands go in a separate `dev` export that the app's UI does not use.
5. **The app requires protocol 1.1+ (`cmds` in HELLO)** [D]: a pre-v1 device gets "update XY Forever", so the client never needs the 1.0 rule. **Stack
   constraints** [D]: TypeScript (strict), ES modules, Vite (localhost dev server, as open-xy uses), Vitest (Node for the client, browser mode for end-to-end
   tests). The UI framework is left open; the browser end-to-end step uses plain DOM so that it assumes none.
6. **Tests without a device** [D]: Node tests open the simulator's TCP port directly (`node:net`). The browser reaches it through a WebSocket-to-TCP relay in
   the Vite dev server (browser end-to-end step), so the simulator needs no WebSocket code. Conformance cases are data that both the Python and the TS runners
   execute (conformance, below).

## Browser facts (re-check before building)
- **Support [U]:** Web Serial runs in Chrome, Edge and Opera on macOS, Windows, Linux and ChromeOS (since Chrome 89). Android: reported shipped around Chrome
  138 (2025), including USB serial; verify. Firefox: nothing shipped (at most a gated prototype). Safari and every iOS browser: none, and WebKit opposes it. It
  needs a secure context: HTTPS, or `http://localhost` / `127.0.0.1` for development. `file://`: unknown. An iframe needs `allow="serial"`. Hosting: any static
  HTTPS host.
- **Picker:** `navigator.serial.requestPort()` needs a click and opens Chrome's chooser; the grant is per origin [U]. Filter
  `{usbVendorId: 0x2367, usbProductId: 0x0021}` [D]. The service enumerates as VID `0x2367` PID `0x0021`, product "OP-XY Boot", interface classes 02 + 0A
  [F ioreg, a device USB log]. The stock OS uses the same PID but exposes no serial interface (MTP is vendor class 0xFF; another mode is PID `0x8021`, class 01)
  [F timelines, `mtp.mjs`], so the filter shows only XY Forever. HARDWARE.md's "class 06" for the OS's MTP disagrees with both sources [F]: fix it in passing.
- **Reconnect:** a restart (`device.reboot`, `menu.continue`, later `usb.mode`) removes the port. Chrome fires `disconnect`, then `connect` when the port
  returns, and `getPorts()` gives it back without the chooser only if the device has a USB serial number; without one, the grant ends at disconnect [U]. The
  macOS name `cu.usbmodem<string>1` implies a serial-number string [I]; the Web Serial device check (below) checks that the grant survives a reboot. The app
  then sends `hello` every 0.5 s, as `xyf_host.connect` does [F].
- **Open:** `port.open({baudRate})` needs a baud rate, which the device ignores [F spec]. Set `bufferSize` to 64 KiB (default 255 B [U]).
  Unknown [U, as in 004]: whether DTR is set on open and whether stock `cdc_write` needs it. Chrome may open the tty exclusively [U]: one tool at a time
  (keepalive.sh, xyf_host, the app), and the app says "close other tools".

Detail: What `protocol/v1.json` lacks, Packages, Reuse in [013-web-app.client.md](013-web-app.client.md); Additions, Split in
[013-web-app.plan.md](013-web-app.plan.md)

## Safety position
A page reaches the device only after the user picks the port for that origin [U], so the threat is a buggy or compromised app. File commands are already no
worse than MTP: the same path policy refuses the brick paths (009), and destructive commands bind their target (004). The gap is running new code. Today
`boot.install`, or `file.write_staged` of an `os.ldr` followed by `library.set_default` and `menu.continue`, runs new code with nobody at the device; stock
needs a hand on the device to flash. So #21's on-device confirmation must: (a) cover every command that changes which code runs next:
`boot.install`/`uninstall`, `library.set_default`/activate, `mods.apply`, and `package.install` of executable kinds; (b) be changeable only on the device; (c)
not accept injected keys (`diag.key`) while it asks; (d) be visible in the protocol: HELLO key `confirm=1`, a `confirm` flag per command, status `declined`
(17), timeouts that include the wait. Proposed default: on for code, off for files [D; open question 2]. Nothing is needed in the spec now; #21 adds this as a
minor bump.
