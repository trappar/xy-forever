# Two agents at once

Two agents may work this repository on one machine at the same time. They cannot message each other, so the
branch and the working tree say who has what.

- **One worktree per agent and item**, outside the workspace folder (the one holding this checkout, `private/` and
  `tools/`): from the main checkout, `git worktree add ../../<workspace>-worktrees/<N> -b <N>-<slug> origin/main`.
  A worktree inside the workspace is watched by an editor opened on it (VS Code's Vitest extension starts a
  watcher for each). Two agents never share a working tree. Both agents' branches and PRs are under one GitHub
  account: resume only the item whose worktree you are in; an item with a worktree is someone's.
- A worktree, wherever it is, finds the same private folder (status notes included), toolchain and
  `xyf.local.json` as the main checkout (`main_checkout` in `xy_forever/paths.py`). Its `.venv` and
  `node_modules` are its own: `mise run setup` there before checks. Never `git stash`: the stash is shared.
- Host tools that open the OP-XY (`uv run xyf-host`, the MTP and USB probes) hold an OS lock on
  `<private>/xy-forever/device.lock` (`xy_forever/device_lock.py`) until they exit, even on a crash. A second
  one waits up to its `--timeout`, then stops with "device busy" naming the holder. The simulator takes no lock.
  One agent talks to the device at a time ([ORCHESTRATION.md](../ORCHESTRATION.md#the-device)).
- `mise run check` runs side by side: pruning (`scripts/clean.py`) skips build folders touched in the last
  hour and keeps each worktree's newest verified build, and receipts are keyed by tree (`--confirm` works from
  any worktree).
