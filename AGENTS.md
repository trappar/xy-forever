# XY Forever: agent guide

XY Forever is a firmware and mod manager for the OP-XY, never a bootloader. It is a chain-loaded
second-stage `os.ldr`: the real 1.2.1 bootloader (never modified) loads it from the
NAND boot partition, and it boots firmware files from `/yaffs2/xy-forever/`.

## Start here

- **Main agent of a session** (asked to "continue development" or "resume work"): if `mise run
  doctor` exits non-zero, setup is unfinished: follow [ONBOARDING.md](ONBOARDING.md) first. Otherwise
  follow [docs/ORCHESTRATION.md](docs/ORCHESTRATION.md): work the GitHub issues (`mise run plan`),
  delegating when available or using its sequential workflow. Device work starts with the
  [first device install](docs/operating/first-install.md) when it has not happened yet.
- **Sub-agent with a brief:** read what the brief names, plus
  [docs/SAFETY.md](docs/SAFETY.md) (**binding**: never brick the developer's OP-XY) before
  anything that reaches the firmware or the device.
- Your status notes, `<private>/xy-forever/status/` (default `../private/`; never in git):
  `README.md` holds device state, installed build, standing approvals, open problems; detail sits in
  leaf files beside it.

**Context is the cost.** Read only what the task needs: an index before its leaves, and the part of a long
file or output that answers the question. A doc over its budget is split into a tree, never squeezed.

| Task | Read |
|---|---|
| Tools, commands, private folder, toolchain, Open-XY upstream | [docs/operating/dev-tools.md](docs/operating/dev-tools.md) |
| Tests, tiers, budgets, the PR gate | [checks](docs/operating/build-install.md#checks) |
| Hardware session, install, reports, working with the developer at the device | [docs/OPERATING.md](docs/OPERATING.md) |
| Design, boot flows, where code lives | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| Stock addresses, keys, display, yaffs, `.tfw`, RE tools | [docs/HARDWARE.md](docs/HARDWARE.md) |
| On-device files and formats | [docs/STORAGE.md](docs/STORAGE.md) |
| Product scope and decisions | [docs/GOALS.md](docs/GOALS.md) |
| Safety constraints, device lessons, approvals | [docs/SAFETY.md](docs/SAFETY.md) |

## Rules for this repository

- **Private data stays out of git:** firmware, keys, decompiled listings, build outputs and device reports
  live in the private folder ([dev tools](docs/operating/dev-tools.md)). Never print or commit key material.
- **Tools:** mise, uv and pnpm pin everything; Python runs via `uv run`; `mise tasks` lists the commands.
  Never change the toolchain pins (`toolchains.json`).
- **Open-XY facts come from upstream**, never from a workspace fork, and name the commit used
  ([dev tools](docs/operating/dev-tools.md#open-xy-upstream)).
- **Recommend, do not defer.** On technical choices, check the premise behind a stated preference
  and correct it if it is wrong, then give one recommendation with its reasons and what is
  uncertain. Do not adopt a view only because of pushback. Product scope, risk to the device and
  approvals stay the developer's.
- **Checks:** after each change, `mise run test-changed`; once per PR, `mise run check`; CI is the merge
  check. None stands in for a device test ([checks](docs/operating/build-install.md#checks)).
- **No flashing scripts.** Host tools talk to XY Forever's USB service (`uv run xyf-host`) and standard MTP;
  test files go in a scratch folder, removed after. `docs/SAFETY.md` rules every device action.
- **Git:** one branch per plan item and one pull request per branch, after claiming the item's
  GitHub issue ([docs/ORCHESTRATION.md](docs/ORCHESTRATION.md)). Never push to `main`; never take an
  item someone else is assigned. Another agent here: [two agents](docs/operating/two-agents.md). No AI
  attribution in commits or PRs. Every new on-device file and wire format carries a version, and its reader
  refuses a newer version with a message, never a crash.
- **Current facts belong in their owning reference or code comment.** Research findings, decisions and
  follow-ups belong in GitHub issues; use Git history for derivation.
- **Keep your status notes current** whenever a build is installed or tested, or the
  device state changes. Where everything else goes: the table in
  [docs/ORCHESTRATION.md](docs/ORCHESTRATION.md). Logs and scratch files never go in the repository.
