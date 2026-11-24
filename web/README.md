# XY Forever Web

The friendly front end for XY Forever, the firmware and mod manager for the OP-XY: a package manager for
Open-XY mods and a file manager, talking to the OP-XY over Web Serial. Desktop Chrome (or Edge) only.

The host never assembles firmware: it sends mod packages and asks the OP-XY to build.

## Run

From the repository root (the tools pinned in `mise.toml`; `pnpm install` covers `web/` and `clients/ts`):

```sh
pnpm install
mise run web         # pnpm run dev in web/: http://127.0.0.1:5173/
```

Open it in Chrome, start the OP-XY into the XY Forever menu, connect USB, press Connect.
For development without a device, start the simulator from the repository root
(`mise run sim`, port 7341; it needs the private stock files, see `../docs/operating/first-install.md`) and open
`http://127.0.0.1:5173/?sim=7341` (the dev server relays a WebSocket to the simulator's TCP port).

## Pages

React 19, Tailwind 4 and shadcn/ui (`src/components/ui`), one component per page in `src/pages`. The connection lives
in a Zustand store outside React (`src/connection.ts`); device data comes through TanStack Query, and every device
action is a mutation in one shared scope, so the OP-XY gets one at a time (`src/device.ts`). The port opens only
from a click.

Each page has its own path (TanStack Router, `src/routing.ts`). Routes include `/connect` (also `/`),
`/device`, `/mods`, `/files` and install stages such as `/install/boot`, `/install/transfer`, `/install/start`
and `/install/stop`. The install stages are listed in `src/install/stages.ts`. The build emits an HTML index
for each route so direct links and reloads work on static hosting at `/`.

- **Connect**: Web Serial, or the simulator with `?sim=PORT`; a clear message in browsers without Web Serial.
- **Install** (#178): installs XY Forever on a stock OP-XY (Open-XY's builder flow: te-boot over Web MIDI, the dev
  menu's MTP over WebUSB, or a drag into Explorer on Windows); XY Forever then sets itself up on the device and the
  page hands off to **Mods**. It installs Stable, or Canary when the site has one and the owner chooses it. The whole flow: [the Install page](docs/install.md).
- **Device**: build, what is running, what boots next, the saved mods (`/yaffs2/xy-forever/config.json`), the content state.
  When a newer XY Forever release is available, the page shows its changes and an Update button. When the site has a
  canary, the page offers a choice of Stable or Canary (experimental; kept in this browser). Clicking Update
  downloads and verifies the release, installs and reads it back, then restarts straight into the XY Forever menu
  and checks the running image. USB control uses the usual device trust prompt.
- **Mods**: packages on the device grouped by kind (synths, drums, samplers, effects, players, extensions) with their
  saved on/off state; add packages from a folder (a `modkit split` generation folder); remove a package or a whole set;
  **Mods on the OP-XY**: the mods it runs and Mod memory, saved as the device's Mods menu does
  (`config.set`: checked and kept, or refused with the reason; built the next time XY Forever starts).
- **Files**: browse `/yaffs2`, download, upload, new folder, rename/move, copy, delete. Manual transfers can change
  `user/` and most of the drive. The page and device keep `bootloader.dev` and the stock `upgrade/` inbox read-only.

File and package removals ask first in plain words.

**Bug report** previews a JSON bundle and downloads that same snapshot when you choose **Download JSON**.
It includes the app version, connection status, the last known XY Forever release, image ID, protocol,
mode and developer-build flag, plus up to 24 recent app actions and their outcomes. The app keeps
this information in memory for the current tab, so the preview is available even after disconnecting.

## Uninstall

Connect in the web app and choose **Uninstall XY Forever** on the **Device** page (device protocol 1.26 or later).
This restores official content, writes the exact official OS 1.2.1 image from the protected `upgrade/` folder to the
boot partition and checks its read-back. It then restores the root `os.ldr`, removes the `xy-forever/` folder with
the library, mods and settings, and requests a restart. Keep the OP-XY powered and connected until it restarts.
If cleanup stops after the verified write, retry while the XY Forever menu still runs. A power cycle at that point
starts stock OS 1.2.1.

## Test

```sh
mise run web-test    # the client's Vitest run, then this app's (test/: config parsing, mod grouping, path rules,
                     # the install checks and steps), then both type checks
mise run web-e2e     # Playwright: the app in the system Chrome against the simulator and fake devices (e2e/)
```

What each covers, budgets, targeted runs and the run against the OP-XY itself: [testing](docs/testing.md).

## The client library

`xy-forever-client` is a pnpm workspace package (`workspace:*`, `clients/ts`). Its `exports` point at its
TypeScript source, so Vite, Vitest and `tsc` use it as it is, with no build step; the dev server's simulator relay
and the e2e tests' simulator helpers come from its `./test/*` export.

## Release

The pinned release image, the canary and the Cloudflare Pages deployment are described in [release](docs/release.md).

## Not built yet

- Firmware library management, firmware and companion install,
  Bluetooth.
- Recursive delete of a non-empty folder, drag and drop upload, upload of folders.

## Licence

MIT, as the rest of the repository: see `../LICENSE`.
