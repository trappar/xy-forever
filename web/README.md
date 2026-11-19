# XY Forever Web

The friendly front end for XY Forever, the boot-menu firmware for the OP-XY: a package manager for
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

- **Connect**: Web Serial, or the simulator with `?sim=PORT`; a clear message in browsers without Web Serial.
- **Device**: build, what is running, what boots next, the saved mods (`/yaffs2/xy-forever/config.txt`), the content state.
- **Mods**: packages on the device grouped by kind (synths, drums, samplers, effects, players, extensions) with their
  saved on/off state; add packages from a folder (a `modkit split` generation folder); remove a package or a whole set;
  **Build on device** (marked low-level, see below).
- **Files**: browse `/yaffs2`, download, upload, new folder, rename/move, copy, delete. Changes are allowed only under
  `/yaffs2/xy-forever/` and `/yaffs2/content/`; the page says so and refuses early what the device would refuse.

Every destructive action asks first in plain words. Nothing offers to write a bootloader.

## Test

```sh
npm test             # Vitest: config parsing, mod grouping, path rules (test/)
npm run typecheck
npm run test:e2e     # the app in the system Chrome (headless) against the simulator (e2e/)
```

`test:e2e` starts `python3 -m xy_forever.sim --virtual-clock` from the repository root on a random port in 21000-31000, with its
state in the private sessions folder (`XYF_PRIVATE` applies; override with `XYF_SIM_STATE`), and removes the state
afterwards. It is skipped with the reason when the simulator cannot run (no private stock files, no clang++).
The repository's suite runs `npm test` here (`tests/test_web.py`) once `npm ci` has run.

## The client library

`xy-forever-client` is a `file:` dependency on `../clients/ts`, used from its TypeScript source through aliases in
`vite.config.ts` and `tsconfig.json` (it has no built `dist/` yet); the dev server's simulator relay and the e2e
test's simulator helpers come from its `test/` folder.

## Not built yet

- **Save over USB.** "Build on device" uses the client's low-level `applyMods` (`mods.apply`): it builds the
  library entry mods-a or mods-b with its content not installed and does not change what boots. Turning mods on or
  off and saving stays in the device's Mods menu until the device offers its Save over USB. The seam is
  `buildOnDevice` / `setBuildOnDevice` in `src/main.ts` (also `window.xyfApp.setBuildOnDevice`).
- Making a built entry the default (`makeDefault`), firmware library management, firmware and companion install,
  XY Forever self-update, Bluetooth.
- Recursive delete of a non-empty folder, drag and drop upload, upload of folders.
- Production hosting (it needs a secure context: `https://` or `localhost`).

## Licence

MIT, as the rest of the repository: see `../LICENSE`.
