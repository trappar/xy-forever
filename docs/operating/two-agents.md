# Two agents at once

Two agents from different tools may work this repository on one machine at the same time. They cannot message
each other, so they coordinate through local files under the private folder; GitHub never learns about agents
(no labels, comments, refs, branch names or PR text). All of it is optional: an agent working alone, or a person,
never runs `claim`, and `plan.py` without `--agent` is unchanged. Sub-agents inside one session still follow the
one-writer rule ([ORCHESTRATION.md](../ORCHESTRATION.md#agents)).

## Agent names

Each agent passes its tool's name as `--agent` from this fixed list: `claude`, `codex`. `plan.py` refuses any
other (`AGENTS` in `xy_forever/claims.py`; change both together).

## Worktrees

Each agent works in its own `git worktree` of the main checkout, one per item:
`git worktree add ../xy-forever-<N> -b <N>-<slug> origin/main`. Two agents never share a working tree.
A worktree finds the same private folder, toolchain, `xyf.local.json` and local `docs/status/` as the main
checkout (`main_checkout` in `xy_forever/paths.py`; `XYF_PRIVATE` and `XYF_TOOLCHAIN` still win). Its
`node_modules` are its own: `npm ci` there before web or client checks. Never `git stash`: the stash is shared.

## Claims

- `python3 scripts/plan.py --agent NAME` splits your items: claimed by NAME (yours: resume only these),
  claimed by another agent (leave them; they leave "Next" too), and unclaimed (interrupted work: claim it
  before you resume it). It removes claims whose pull request is merged or closed, and marks a claim whose
  branch has had no commits of its own for two days as stale.
- `python3 scripts/plan.py claim <N> --agent NAME`, in the item's new worktree, before
  `gh issue edit <N> --add-assignee @me`. The claim is `<private>/xy-forever/claims/<N>.json` (versioned:
  agent, branch, worktree, time), made by exclusive create: of two agents one wins, and the other is refused
  with the holder's name. Refused: remove your worktree (`git worktree remove`) and take the next item.
- `python3 scripts/plan.py release <N> --agent NAME` when you stop an item that has no pull request
  (blocked, unassigned). A merged or closed pull request releases it by itself. Another agent's stale
  claim: ask the developer before releasing it (`release <N>` without `--agent`).

## The device and builds

- Host tools that open the OP-XY (`xyf_host.py`, the MTP and USB probes) hold an OS lock on
  `<private>/xy-forever/device.lock` (`xy_forever/device_lock.py`) until they exit, even on a crash. A second
  one waits up to its `--timeout`, then stops with "device busy" naming the holder. The simulator takes no lock.
  Device items still need the developer: work one agent's at a time.
- `scripts/check.py` never prunes a build folder touched in the last 15 minutes, so checks in two worktrees can
  run at once. Receipts are keyed by tree, so `--confirm` works from any worktree.
