# Testing the web app

The tests of the web app ([the web app](../README.md)); the client library's own are in
[its README](../../clients/ts/README.md). No test reaches a real OP-XY but the device run at the end.

## Unit tests and type check

`mise run web-test` runs the client's Vitest run, then this app's (`pnpm test`: `test/*.test.ts` in Node, under
1 s) and its type check (`pnpm run typecheck`). One file or test: `pnpm exec vitest run test/install.test.ts -t 'Mac'`.

## Browser tests

`mise run web-e2e` (`pnpm run test:e2e`, Playwright; CI runs it when `web/`, `clients/ts` or the protocol change)
drives the app in the system Chrome, headless, never a downloaded browser:

- **One dev server per run.** `playwright.config.ts` starts Vite once (`webServer`, a free port passed to the
  workers); the specs share it, so a cold dependency cache is optimized once.
- **Projects.** `app` (`e2e/app.e2e.ts`: connect, the Device page, Files, an update that restarts into the menu, the
  offline bug report) against the simulator, Web Serial off in Chrome. `install` (`e2e/install.e2e.ts`: every
  refusal and guidance branch of the Install page against fake te-boot MIDI and a fake dev-menu MTP device,
  `test/fake-devices.ts`; `e2e/install-sim.e2e.ts`: the install's end against the simulator, a stock OP-XY just
  after Track 5, through the client's fake Web Serial port, `clients/ts/test/fake-serial.ts`), Web Serial and WebUSB
  off. `bounds` (`e2e/hang-bound.e2e.ts`): a nested run of a planted hung page (`e2e/planted-hang.ts`) must fail,
  naming that test, within its bound.
- **The simulator.** The specs start `python3 -m xy_forever.sim` on random ports, state in the private sessions
  folder (`XYF_PRIVATE` applies; `XYF_SIM_STATE` overrides app.e2e.ts's), removed afterwards; skipped with the
  reason when it cannot run (no private stock files, no clang++). Device time moves only when a test moves it; the
  page's own timers run on Playwright's clock.
- **Budgets.** 15 s a test, 90 s the run (`globalTimeout`); the run takes about 15 s.
- **Targeted runs.** `pnpm exec playwright test --project install`, `pnpm exec playwright test e2e/app.e2e.ts -g
  'offline'` (from `web/`, under `uv run` for the simulator specs).

`tests/test_web.py` in the Python suite only checks that the web build output stays untracked.

## The app against the OP-XY

`web/scripts/device-browser-test.ts` drives the app in Chrome with no human step: Connect, the Device page, list
`/yaffs2/xy-forever`, upload a scratch file, download and compare it, delete it, list mod packages, disconnect.
PASS or FAIL per step, `result.json` and a screenshot per step in `--out`.

```sh
node scripts/device-browser-test.ts --out DIR --sim PORT   # the simulator through the dev server's relay
node scripts/device-browser-test.ts --out DIR --serial     # the OP-XY over Web Serial
node scripts/device-browser-test.ts --grant                # once, headed: pick the OP-XY in Chrome's chooser
```

`--serial` cannot answer Chrome's serial port chooser (no automation can), so it needs a Chrome profile
(`--profile`, default `<private>/xy-forever/chrome-device-test`; `<private>` is `XYF_PRIVATE` or `../private`)
that already holds a grant for the dev server's origin `http://127.0.0.1:5173`. Two ways to get one:

- once by hand: `--grant` opens that profile in a visible Chrome; pick the OP-XY in the list once. It waits for
  the grant, checks that a new launch connects without the chooser, and exits.
- the Chrome policy `SerialAllowUsbDevicesForUrls` (the vendor and product id of `USB_FILTERS`, that origin):
  no pick, but on macOS it is a managed preference and needs admin rights.

Serial runs hold the shared OP-XY device lock. The script prints a `NEED:` line if the granted port or lock is
unavailable, or if **Ask before USB control** is on. It stays a script, not a Playwright project: its grant step is
headed and waits for a person, and its persistent profile is per origin.
