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
- **Blackfin toolchain:** `../tools/blackfin` (`mise run setup` downloads it). Elsewhere:
  `uv run scripts/setup.py init --toolchain DIR` (checked against its pins, recorded in `xyf.local.json`), or
  `XYF_TOOLCHAIN` for one shell. Never change its pins (`toolchains.json`).
- A worktree finds both, and `xyf.local.json`, through the main checkout ([two agents](two-agents.md)).

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
