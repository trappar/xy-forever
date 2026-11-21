# XY Forever Web

The friendly front end for XY Forever, the firmware and mod manager for the OP-XY: a package manager for
Open-XY mods and a file manager, talking to the OP-XY over Web Serial. Desktop Chrome (or Edge) only.

The host never assembles firmware: it sends mod packages and asks the OP-XY to build.

## Run

From `web/` (Node 22.18+):

```sh
npm ci
npm run dev          # http://127.0.0.1:5173/
```

Open it in Chrome, start the OP-XY into the XY Forever menu, connect USB, press Connect.
For development without a device, start the simulator from the repository root
(`python3 -m xy_forever.sim`, port 7341; it needs the private stock files, see `../ONBOARDING.md`) and open
`http://127.0.0.1:5173/?sim=7341` (the dev server relays a WebSocket to the simulator's TCP port).

## Pages

Each page has its own path (`src/router.ts`, History API). Routes include `/connect` (also `/`),
`/device`, `/mods`, `/files` and install stages such as `/install/boot`, `/install/transfer`, `/install/start`
and `/install/stop`. The stage and page registry lives in `src/routes.ts`. The build emits an HTML index
for each route so direct links and reloads work on static hosting. The routing base is `/` in local
development and `/xy-forever/` for GitHub Pages.

- **Connect**: Web Serial, or the simulator with `?sim=PORT`; a clear message in browsers without Web Serial.
- **Install** (#178): installs XY Forever on a stock OP-XY, no other apps. Its flow, wording, MTP FAQ and "start fresh
  with OS 1.2.1" path are Open-XY's builder flow (`src/install/page.ts`), and its te-boot (Web MIDI) and dev-menu MTP
  (WebUSB) code is Open-XY's `ferret.mjs` and `mtp.mjs` (`src/vendor/open-xy/`, MIT, notices kept; the Ferret copy has
  no disable command). The page reads te-boot's identity and refuses a 1.2.5 bootloader or unknown firmware (older than
  1.2.1: the official 1.2.1 update, named exactly), switches the dev menu on (never off), then copies our `os.ldr`:
  over WebUSB on a Mac (after checking that the stock `upgrade/firmware/blackfin/bin/os.ldr` is there, by its stock 1.2.1 size, and
  the free storage; read back), or downloaded and dragged into Explorer on Windows (after the owner finds that stock
  file in Explorer, about 2,318 KB). Track 5 and a power-on are the
  owner's. The page never reads or moves the stock OS: at its first start XY Forever adopts that file on the device
  (hashed there, copied into library entry `opxy-121`, made the default, then booted; `docs/storage/jobs.md`). The
  page reads where that setup stands over Web Serial (`src/install/finish.ts`: `job.status`, the entry, the default):
  while it runs it shows it, a refusal shows the device's own words (removing the entry is advised only when the
  device says it is incomplete), and once the entry is there and the default names a library entry (it, or one with
  mods) the page hands
  the connection to the app and opens **Mods** with a dismissible "XY Forever is installed" notice. The last step tells
  the owner once that holding PLAY at power-on opens the XY Forever menu (the plain first start boots OS 1.2.1, so the
  page meets XY Forever there). The pure checks: `src/install/checks.ts`. The choices a stage needs (the computer, the
  file sent, a refusal) are kept in `sessionStorage` for a reload (going Back to the copy step sends the file again);
  a reload at a te-boot stage asks te-boot again. At every stage (on load, on each stage change, when a serial port
  appears, and every few seconds at the last stage) the page looks for an OP-XY running XY Forever on ports Chrome
  already granted, never opening the chooser unasked, and jumps ahead when it answers; **Find my OP-XY** (a click,
  which may open the chooser) does the same. It never looks while a te-boot check over Web MIDI or the MTP copy is
  under way, only once that ends (te-boot is MIDI only; the serial "OP_XY Boot" is XY Forever's own fallback service).
  Either way it follows the setup as above.
- **Device**: build, what is running, what boots next, the saved mods (`/yaffs2/xy-forever/config.json`), the content state.
  When a newer XY Forever release is available, the page shows its changes and an Update button. Clicking Update
  downloads and verifies the release, installs and reads it back, then restarts straight into the XY Forever menu
  and checks the running image. USB control uses the usual device trust prompt.
- **Mods**: packages on the device grouped by kind (synths, drums, samplers, effects, players, extensions) with their
  saved on/off state; add packages from a folder (a `modkit split` generation folder); remove a package or a whole set;
  **Mods on the OP-XY**: the mods it runs and Mod memory, saved as the device's Mods menu does
  (`config.set`: checked and kept, or refused with the reason; built the next time XY Forever starts).
- **Files**: browse `/yaffs2`, download, upload, new folder, rename/move, copy, delete. Manual transfers can change
  `user/` and most of the drive. The page and device keep `bootloader.dev` and the stock `upgrade/` inbox read-only.

File and package removals ask first in plain words.

## The release image

Install and Update use the `os.ldr` whose SHA-256 `src/install/release.json` pins, fetched from this site at
`release/os.ldr`. `python3 scripts/web_release.py [BUILD] --notes "What changed"` (from the repository root) takes a verified product
build, checks it shares no bytes with the stock pair (`xy_forever/stock_bytes.py`; `xyf_build.py --verify` checks it
too), writes the pin (committed in a release pull request) and stages the image in `public/release/` (gitignored);
`--check` says whether the staged image matches the pin. Without a pin the page says there is nothing to install.

## GitHub Pages

The planned launch URL is `https://trappar.github.io/xy-forever/`. Deployment and the public firmware
asset are pending. A local Install or Update run requires a staged image matching the pin.
The app and the pinned image share that project path. The project and third-party notices are served at `LICENSES.txt`. Only `web/dist/` goes into the Pages artifact.

From the repository root, stage the `os.ldr` matching the checked-in pin in `web/public/release/`.
Use the locally verified build or download the same asset from the published `v<version>` release.
Then run the deployment checks with Chrome installed.

```sh
python3 scripts/web_release.py --check
npm --prefix web run build:pages
npm --prefix web run smoke:pages
```

The smoke test serves the built files at `/xy-forever/` on localhost without a fallback for missing
paths. It checks direct links, reloads, back and forward, and the Install page's download of the
pinned image.

At launch, select [GitHub Actions as the Pages source](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site).
Attach the staged `os.ldr` to the GitHub release tagged `v<version>` from `release.json`, then run
**Deploy web app to GitHub Pages** on `main`. The workflow fetches that one asset, checks it against the
pin, builds and runs the smoke test before deploying. It uses Node dependencies and the verified
release asset. After it finishes, open the HTTPS site, reload an install stage and check the
SHA-256 of `/xy-forever/release/os.ldr` against the release pin.

**Bug report** previews a JSON bundle and downloads that same snapshot when you choose **Download JSON**.
It includes the app version, connection status, the last known XY Forever release, image ID, protocol,
mode and developer-build flag, plus up to 24 recent app actions and their outcomes. The app keeps
this information in memory for the current tab, so the preview is available even after disconnecting.

## Test

```sh
npm test             # Vitest: config parsing, mod grouping, path rules, the install checks and steps (test/)
npm run typecheck
npm run test:e2e     # the app in the system Chrome (headless) against the simulator, and the Install page (e2e/)
```

`test:e2e` starts `python3 -m xy_forever.sim --virtual-clock` from the repository root on a random port in 21000-31000, with its
state in the private sessions folder (`XYF_PRIVATE` applies; override with `XYF_SIM_STATE`), and removes the state
afterwards. It is skipped with the reason when the simulator cannot run (no private stock files, no clang++).
`e2e/install.e2e.ts` needs no simulator: fake te-boot MIDI and a fake dev-menu MTP device (`test/fake-devices.ts`)
stand in for the OP-XY, with Web Serial and WebUSB switched off in Chrome. `e2e/install-sim.e2e.ts` runs the
install's end against the simulator (a stock OP-XY just after Track 5: `--fresh`) through the client's fake Web Serial
port (Chrome's 255-byte default buffer, the chooser, granted ports): the setup on the device, then the hand-off to
Mods; a missing stock file refused. The repository's suite runs `npm test` here and both install browser tests
(`tests/test_web.py`) once `npm ci` has run.

## The client library

`xy-forever-client` is a `file:` dependency on `../clients/ts`, used from its TypeScript source through aliases in
`vite.config.ts` and `tsconfig.json` (it has no built `dist/` yet); the dev server's simulator relay and the e2e
test's simulator helpers come from its `test/` folder.

## Not built yet

- Firmware library management, firmware and companion install,
  Bluetooth, uninstall (#179).
- Recursive delete of a non-empty folder, drag and drop upload, upload of folders.

## Licence

MIT, as the rest of the repository: see `../LICENSE`.
