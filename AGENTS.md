# XY Forever: agent guide

XY Forever is a firmware and mod manager for the OP-XY, never a bootloader. It is a chain-loaded
second-stage `os.ldr`: the real 1.2.1 bootloader (never modified) loads it from the
NAND boot partition, and it boots firmware files from `/yaffs2/xy-forever/`.

## Start here

- **Main agent of a session** (asked to "continue development" or "resume work"): if `mise run
  doctor` exits non-zero, setup is unfinished: follow [ONBOARDING.md](ONBOARDING.md) first. Otherwise
  follow [docs/ORCHESTRATION.md](docs/ORCHESTRATION.md): work the GitHub issues (`mise run plan`).
  With sub-agents you never read or edit source, tests or build scripts: every implementation and every
  question about the code goes to a sub-agent. Without any, work items yourself in sequence. Device work
  starts with the [first device install](docs/operating/first-install.md) when it has not happened yet.
- **Sub-agent with a brief:** read what the brief names, plus
  [docs/SAFETY.md](docs/SAFETY.md) (**binding**: never brick the developer's OP-XY) before
  anything that reaches the firmware or the device.
- Your status notes, `<private>/xy-forever/status/` (default `../private/`; never in git):
  `README.md` holds device state, installed build, approvals, open problems; detail sits in
  leaf files beside it.

**Context is the cost.** Read only what the task needs: an index before its leaves, and the part of a long
file or output that answers the question. A doc over its budget is split into a tree, never squeezed.

| Task | Read |
|---|---|
| Tools, commands, private folder, toolchain, Open-XY upstream | [docs/operating/dev-tools.md](docs/operating/dev-tools.md) |
| Tests, tiers, budgets, the PR gate | [checks](docs/operating/checks.md) |
| Hardware session, install, reports, working with the developer at the device | [docs/OPERATING.md](docs/OPERATING.md) |
| Design, boot flows, where code lives | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| Stock addresses, keys, display, yaffs, `.tfw`, RE tools | [docs/HARDWARE.md](docs/HARDWARE.md) |
| On-device files and formats | [docs/STORAGE.md](docs/STORAGE.md) |
| Product scope and decisions | [docs/GOALS.md](docs/GOALS.md) |
| Safety constraints, device lessons, host access | [docs/SAFETY.md](docs/SAFETY.md) |

## Rules for this repository

- **Private data stays out of git:** firmware and other teenage engineering bytes, keys, decompiled listings,
  build outputs and device reports live in the private folder ([dev tools](docs/operating/dev-tools.md)). Never print or commit key material.
- **Tools:** mise, uv and pnpm pin everything; Python runs via `uv run`; `mise tasks` lists the commands.
  Never change the toolchain pins (`toolchains.json`).
- **Open-XY facts come from upstream**, never from a workspace fork, and name the commit used
  ([dev tools](docs/operating/dev-tools.md#open-xy-upstream)).
- **Checks:** after each change, `mise run test-changed`; once per PR, `mise run check` on a committed tree (lint, the
  build, every tier but the sweeps); CI is the merge check. None stands in for a device test ([checks](docs/operating/checks.md)).
- **The device:** any agent may take the session lease, one at a time; `device:risky` items run only with the developer present
  ([docs/ORCHESTRATION.md](docs/ORCHESTRATION.md#the-device)). `docs/SAFETY.md` rules every device action.
- **Git:** one branch per plan item and one pull request per branch ([docs/ORCHESTRATION.md](docs/ORCHESTRATION.md)),
  each in its own worktree outside the workspace folder; never `git stash`
  ([two agents](docs/operating/two-agents.md)). Never push to `main`. No AI attribution in commits or PRs.
- **Current facts belong in their owning reference or code comment.** Research findings, decisions and
  follow-ups belong in GitHub issues; use Git history for derivation.
- **Keep your status notes current** whenever a build is installed or tested, or the
  device state changes. Where everything else goes: the table in
  [docs/ORCHESTRATION.md](docs/ORCHESTRATION.md). Logs and scratch files never go in the repository.
