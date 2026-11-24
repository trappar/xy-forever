# Development tools

Part of [Operating the device](../OPERATING.md). Setting a workspace up: [ONBOARDING.md](../../ONBOARDING.md).

## Tools

- **Pinned:** mise installs Python, uv, Node, pnpm, gh and lefthook (`mise.toml`; checksums in `mise.lock`).
  `mise tasks` lists the commands; `mise run TASK --help` shows a task's arguments. In a shell without
  `mise activate`, prefix commands with `mise exec --`.
- **Python** runs via `uv run` (`uv.lock`; `xy_forever` is installed in `.venv`, no `sys.path` edits): the
  tools as `uv run xyf-host`, `uv run xyf-build`, `uv run xyf-package` (each with `--help`), scripts as
  `uv run scripts/NAME.py`, modules as `uv run -m xy_forever.NAME`.
- **JS:** pnpm, one workspace (`web/`, `clients/ts`; `pnpm-lock.yaml`). The packages use TypeScript 7; the
  root pins `typescript` to the 6.0 alias (`npm:@typescript/typescript6`) because typescript-eslint needs
  TypeScript's JavaScript API, which 7 (the native compiler) does not have yet.
- **TypeScript work** (`web/`, `clients/ts`): after each change `mise run web-test-changed` (the Vitest tests
  your changes affect); `mise run test-changed` runs those too, beside the Python and C++ ones. `mise run
  web-test` runs them all, `mise run web-e2e` the browser tests.
- **Lint and format:** `mise run lint`, `mise run format`; the pre-commit hook (`lefthook.yml`) runs them on
  staged files.
- **Checks:** [checks.md](checks.md).

## Folders outside the checkout

- **Private folder:** `../private/` (layout: `xy_forever/paths.py`): firmware, keys, decompiled listings,
  build outputs, device reports, sessions, your status notes. Firmware may also go to the checkout's
  gitignored `build/`. Elsewhere: `uv run scripts/setup.py init --private DIR` (recorded in `xyf.local.json`),
  or `XYF_PRIVATE` for one shell.
- A worktree finds it, and `xyf.local.json`, through the main checkout ([two agents](two-agents.md)).

## Docker

Every firmware build, and every test that runs the Blackfin toolchain, runs in one pinned Linux image
(`xy_forever/container.py`), so a tree gives the same `os.ldr` on every OS and in CI. The host keeps the firmware
cache: a cached `mise run build` never starts the container.

- **What each OS needs:** macOS: Docker Desktop. Linux: Docker Engine, your user in the `docker` group (the
  container runs as you, so what it writes is yours). Windows: Docker Desktop with the WSL2 backend; clone, run
  mise and every command inside a WSL2 distribution (a checkout on the Windows drive is slow through the mount).
- **The image:** `docker/toolchain.Dockerfile` builds Open-XY's toolchain from its pinned source (their
  `tools/gcc/build-linux.sh`) plus the Python and uv of `mise.toml` (change them together). `toolchain-image.yml`
  publishes it to `ghcr.io/trappar/xy-forever-toolchain` for linux/amd64 and linux/arm64, each checked against its
  tool hashes; `toolchains.json` pins its digest ("image"). `mise run setup` pulls it; by hand: `uv run -m
  xy_forever.container pull`, or any command in it: `uv run -m xy_forever.container run xyf-build --xyf-mtp-usb
  --out build/mtp-usb`.
- **Access:** while the package is private, a pull logs Docker in to ghcr.io with your gh login, which needs
  `gh auth refresh -h github.com -s read:packages` once. CI pulls with its `GITHUB_TOKEN`.
- **A toolchain change** is on purpose and proved: publish the new image (a push that changes the Dockerfile), pin
  its digest and tool hashes, and show in the pull request each build mode's `os.ldr` SHA-256 from the old image
  and the new one, on both platforms.

## Open-XY upstream

Open-XY facts come from upstream: `../private/xy-forever/open-xy-upstream/`, made by `setup.py fixtures`
(fetch it first when a question needs current upstream), naming the commit used. A workspace `../open-xy` is a
fork (the developer's, for work on it): it shows what that fork does, never what upstream does. If the
developer cites an open-xy fact upstream does not show, ask where they saw it (a branch, a pull request, the
website).

## Commands that are not mise tasks

```sh
uv run xyf-host status                      # the device over USB (--sim 127.0.0.1:7341,7342: the simulator)
uv run -m xy_forever.census REPORT.bin      # decode a device report
uv run scripts/setup.py import-stock --from-device   # the stock pair off the OP-XY (first device install)
```
