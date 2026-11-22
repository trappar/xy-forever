# Two agents at once

Two agents from different tools may work this repository on one machine at the same time. They cannot message
each other, so they coordinate on GitHub: the assignee and an `agent:<name>` label on the item's issue, where
`<name>` is a short name for the tool. An agent working alone, or a person, needs no label, and
`plan.py` without `--agent` reads a labelled item as taken.

## Worktrees

Each agent works in its own `git worktree` of the main checkout, one per item:
`git worktree add ../xy-forever-<N> -b <N>-<slug> origin/main`. Two agents never share a working tree.
A worktree finds the same private folder (status notes included), toolchain and `xyf.local.json` as the main
checkout (`main_checkout` in `xy_forever/paths.py`; `XYF_PRIVATE` and `XYF_TOOLCHAIN` still win). Its
`.venv` and `node_modules` are its own: `uv sync` and `pnpm install` there (or `mise run setup`) before checks. Never `git stash`: the stash is shared.

## Taking an item

- `mise run plan -- --agent NAME` splits your items: labelled `agent:NAME` (yours: resume only these),
  labelled for another agent (leave them; they leave "Next" too), and assigned to you with no agent label
  (interrupted work: label it before you resume it).
- Take an item with `gh issue edit <N> --add-assignee @me --add-label agent:NAME` (the first time on a
  repository, `gh label create agent:NAME`). Look again right after: if another `agent:` label is there too,
  the agent whose label came first keeps it; the other removes its label and takes the next item.
- Stopping an item that has no pull request (blocked, unassigned): `gh issue edit <N> --remove-label
  agent:NAME`. Another agent's label on an item that looks abandoned: ask the developer before removing it.

## The device and builds

- Host tools that open the OP-XY (`xyf_host.py`, the MTP and USB probes) hold an OS lock on
  `<private>/xy-forever/device.lock` (`xy_forever/device_lock.py`) until they exit, even on a crash. A second
  one waits up to its `--timeout`, then stops with "device busy" naming the holder. The simulator takes no lock.
  Device items still need the developer: work one agent's at a time.
- `scripts/check.py` runs side by side; its pruning skips build folders touched in the last 15 minutes.
  Receipts are keyed by tree: `--confirm` works from any worktree.
