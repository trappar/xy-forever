# XY Forever: agent guide

XY Forever is a boot menu and firmware manager for the OP-XY. It is a chain-loaded
second-stage `os.ldr`: the real 1.2.1 bootloader (never modified) loads it from the
NAND boot partition, and it boots firmware files from `/yaffs2/xy-forever/`.

## Start here

- **Main agent of a session** (asked to "continue development" or "resume work"): if `python3
  scripts/setup.py doctor` exits non-zero, or your local `docs/status/README.md` is missing or records no
  standing-approvals answer yet, onboarding is unfinished: follow [ONBOARDING.md](ONBOARDING.md) first.
  Otherwise you are the orchestrator: read [docs/ORCHESTRATION.md](docs/ORCHESTRATION.md) and follow it;
  the plan is the GitHub issues (`python3 scripts/plan.py`). Delegate code work when available;
  otherwise use its sequential workflow.
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
| Lessons from device tests (what failed, the rule it produced) | [docs/HISTORY.md](docs/HISTORY.md) |

## Rules for this repository

- **Private data stays out of git.** Firmware, keys, decompiled listings, build outputs and device
  reports live in `../private/` (layout: `xy_forever/paths.py`; set up by `scripts/setup.py`),
  outside every checkout. Never print or commit key material.
- **Python 3.9+ and toolchain:** the authenticated Blackfin toolchain at `../tools/blackfin` (or
  `XYF_TOOLCHAIN`; `setup.py bootstrap` downloads it). Never change its pinned hashes (`toolchains.json`).
- **Open-XY facts come from a fresh upstream clone** (`../private/xy-forever/open-xy-upstream/`,
  made by `setup.py fixtures`; fetch it again when a question needs current upstream) and name
  the commit used. Never from another local open-xy checkout. If the developer cites an open-xy
  fact upstream does not show, ask where they saw it (a branch, a pull request, the website).
- **Recommend, do not defer.** On technical choices, check the premise behind a stated preference
  and correct it if it is wrong, then give one recommendation with its reasons and what is
  uncertain. Do not adopt a view only because of pushback. Product scope, risk to the device and
  approvals stay the developer's.
- **Checks:** run the unit tests for every change: `python3 scripts/run_tests.py` (`--times`: the
  slowest; in order, everything: Quick reference). Host bridges and the simulator are cached per input
  hash in `../private/xy-forever/sessions/`, so a run after a source change is slower. Also `python3
  xyf_build.py --xyf-product …` plus `--verify` for anything that reaches the firmware. A change meant
  to be behaviour-neutral (even removing unused code) must leave every build mode's candidate SHA-256
  unchanged: `python3 scripts/neutral.py [REF]`.
- **Source files stay under 32 KB** (`tests/test_source_budget.py`): to grow a file on its over-cap list,
  first split out the part you touch.
- **No flashing scripts.** Host tools talk only to XY Forever's own USB service (`xyf_host.py`), and
  may read files, never write, over the stock dev-menu MTP. `docs/SAFETY.md` rules every device action.
- **Git:** one branch per plan item and one pull request per branch, after claiming the item's
  GitHub issue ([docs/ORCHESTRATION.md](docs/ORCHESTRATION.md)). Never push to `main`; never take an
  item someone else is assigned. Until the repository has its GitHub remote, commit locally on
  `main`. No AI attribution in commit messages. Every new on-device file and wire
  format carries a version, and its reader refuses a newer version with a message, never a crash.
- **Keep your local `docs/status/README.md` current** whenever a build is installed or tested, or the
  device state changes. Where everything else goes: the table in
  [docs/ORCHESTRATION.md](docs/ORCHESTRATION.md). Logs and scratch files never go in the repository.

## Quick reference

```sh
python3 scripts/setup.py doctor                         # private folder: stock files, fixtures (bootstrap, import-stock)
python3 scripts/plan.py                                 # the plan: next items, decisions waiting (--all: blocked, threads)
python3 scripts/run_tests.py --jobs 6                   # unit tests in parallel: ~1.5 min, 1038 tests (busy machine: rerun a timing flake)
python3 -m unittest discover -s tests                   # the same in order: ~8 min
XYF_EXHAUSTIVE=1 python3 scripts/run_tests.py --jobs 6  # everything (+ exhaustive bit flips): ~1-2 min
python3 xyf_build.py --xyf-product --out ../private/xy-forever/builds/xy-forever-product-0NN-001
python3 xyf_build.py --verify --out ../private/xy-forever/builds/xy-forever-product-0NN-001
python3 scripts/neutral.py                              # working tree vs HEAD: same candidates?
python3 xyf_host.py status                              # device over USB
python3 -m xy_forever.sim                               # device simulator (127.0.0.1:7341; xyf_host.py --sim)
(cd web && npm ci && npm run dev)                       # web app on 127.0.0.1:5173 (?sim=7341); npm test, npm run test:e2e
python3 scripts/conformance.py --sim 127.0.0.1:7341     # protocol conformance suite (no --sim: OP-XY, safe cases)
python3 -m xy_forever.census REPORT.bin                 # decode a device report
python3 ui_preview.py                                   # menu screens -> ../private/xy-forever/previews
```
