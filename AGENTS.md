# XY Forever: agent guide

XY Forever is a firmware and mod manager for the OP-XY, never a bootloader. It is a chain-loaded
second-stage `os.ldr`: the real 1.2.1 bootloader (never modified) loads it from the
NAND boot partition, and it boots firmware files from `/yaffs2/xy-forever/`.

## Start here

- **Main agent of a session** (asked to "continue development" or "resume work"): if `mise run
  doctor` exits non-zero, or your local `docs/status/README.md` is missing or records no
  standing-approvals answer yet, onboarding is unfinished: follow [ONBOARDING.md](ONBOARDING.md) first.
  Otherwise follow [docs/ORCHESTRATION.md](docs/ORCHESTRATION.md): work the GitHub issues
  (`mise run plan`), delegating when available or using its sequential workflow.
- **Sub-agent with a brief:** read what the brief names, plus
  [docs/SAFETY.md](docs/SAFETY.md) (**binding**: never brick the developer's OP-XY) before
  anything that reaches the firmware or the device.
- Your local `docs/status/` (gitignored): `README.md` holds device state, installed build, standing
  approvals, open problems; longer detail sits in leaf files beside it.

**Context is the cost.** Read only what the task needs: an index before its leaves, `grep -n` then ranges
(at most 120 lines), command output through `tail`. A doc over its budget is split into a tree, never squeezed.

| Task | Read |
|---|---|
| Hardware session, install, reports, working with the developer at the device | [docs/OPERATING.md](docs/OPERATING.md) |
| Design, boot flows, where code lives | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| Stock addresses, keys, display, yaffs, `.tfw`, RE tools | [docs/HARDWARE.md](docs/HARDWARE.md) |
| On-device files and formats | [docs/STORAGE.md](docs/STORAGE.md) |
| Product scope and decisions | [docs/GOALS.md](docs/GOALS.md) |
| Current safety constraints and device lessons | [docs/SAFETY.md](docs/SAFETY.md) |

## Rules for this repository

- **Private data stays out of git.** Firmware, keys, decompiled listings, build outputs and device
  reports live in `../private/` (layout: `xy_forever/paths.py`; set up by `scripts/setup.py`); firmware may
  also go to the checkout's gitignored `build/`. Never print or commit key material.
- **Tools:** mise pins Python, uv, Node, pnpm, gh (`mise.toml`); uv the Python dependencies (`uv.lock`),
  pnpm the JS ones (`web/`, `clients/ts`). `mise tasks`: the commands. The Blackfin toolchain at
  `../tools/blackfin` (or `XYF_TOOLCHAIN`; `mise run setup` gets it): never change its pins (`toolchains.json`).
- **Open-XY facts come from a fresh upstream clone** (`../private/xy-forever/open-xy-upstream/`,
  made by `setup.py fixtures`; fetch it again when a question needs current upstream) and name
  the commit used. Never from another local open-xy checkout. If the developer cites an open-xy
  fact upstream does not show, ask where they saw it (a branch, a pull request, the website).
- **Recommend, do not defer.** On technical choices, check the premise behind a stated preference
  and correct it if it is wrong, then give one recommendation with its reasons and what is
  uncertain. Do not adopt a view only because of pushback. Product scope, risk to the device and
  approvals stay the developer's.
- **Checks:** after each change, the tests it affects: `mise run test-changed` (or `test-one TARGET`; `mise run
  test`, the fast tier, any time). Once per PR, `mise run check` (the gate: build, `--verify`, type checks, fast
  and gate tiers, under 30 s); CI is the merge check. Budgets fail a slow test: make it faster or move its tier.
  Neither stands in for a device test. [Checks](docs/operating/build-install.md#checks).
- **No flashing scripts.** Host tools talk to XY Forever's USB service (`xyf_host.py`) and standard MTP;
  test files go in a scratch folder, removed after. `docs/SAFETY.md` rules every device action.
- **Git:** one branch per plan item and one pull request per branch, after claiming the item's
  GitHub issue ([docs/ORCHESTRATION.md](docs/ORCHESTRATION.md)). Never push to `main`; never take an
  item someone else is assigned. Another agent here: own worktree and an `agent:<name>`
  label ([two agents](docs/operating/two-agents.md)). No AI attribution in commit messages. Every new on-device file and wire
  format carries a version, and its reader refuses a newer version with a message, never a crash.
- **Current facts belong in their owning reference or code comment.** Research findings, decisions and
  follow-ups belong in GitHub issues; use Git history for derivation. Never create repository research
  reports, numbered decision records, ADRs, or replacement archives/indexes.
- **Keep your local `docs/status/README.md` current** whenever a build is installed or tested, or the
  device state changes. Where everything else goes: the table in
  [docs/ORCHESTRATION.md](docs/ORCHESTRATION.md). Logs and scratch files never go in the repository.

## Quick reference

```sh
mise run setup                              # tools, dependencies, private folder, toolchain, fixtures (rerun: safe)
mise run doctor                             # what is set up, what will skip
mise run plan                               # the plan: next items, decisions waiting (--all: blocked, threads)
mise run test                               # the fast tier (~2 s); test-changed, test-one, test-failed, test-watch
mise run test-all                           # every tier, simulator included (~1 min; CI runs it)
mise run check                              # once per PR: the gate (< 30 s)
mise run build && mise run verify           # product build into ../private/xy-forever/builds/current (check.py: builds/<image>)
mise run neutral                            # working tree vs HEAD: same candidates?
uv run xyf_host.py status                   # device over USB
mise run sim                                # device simulator (127.0.0.1:7341; xyf_host.py --sim)
mise run web                                # web app on 127.0.0.1:5173 (?sim=7341); mise run web-test, web-e2e
mise run conformance                        # protocol conformance suite on the simulator
uv run -m xy_forever.census REPORT.bin      # decode a device report
mise run preview                            # menu screens -> ../private/xy-forever/previews
mise run clean                              # old private sessions and builds (dry run; --yes deletes)
```
