# Two agents at once

Two agents may work this repository on one machine at the same time. They cannot message each other, so the
GitHub assignee and the working tree say who has what.

- **One worktree per agent and item:** `git worktree add ../xy-forever-<N> -b <N>-<slug> origin/main`. Two
  agents never share a working tree. "Yours, in progress" in `mise run plan` lists both agents' items (one
  GitHub account): resume only the item whose worktree you are in; a claimed item with a worktree is someone's.
- A worktree finds the same private folder (status notes included), toolchain and `xyf.local.json` as the
  main checkout (`main_checkout` in `xy_forever/paths.py`). Its `.venv` and `node_modules` are its own:
  `mise run setup` there before checks. Never `git stash`: the stash is shared.
- Host tools that open the OP-XY (`uv run xyf-host`, the MTP and USB probes) hold an OS lock on
  `<private>/xy-forever/device.lock` (`xy_forever/device_lock.py`) until they exit, even on a crash. A second
  one waits up to its `--timeout`, then stops with "device busy" naming the holder. The simulator takes no lock.
  Device items still need the developer: work one agent's at a time.
- `mise run check` runs side by side: pruning (`scripts/clean.py`) skips build folders touched in the last
  hour and keeps each worktree's newest verified build, and receipts are keyed by tree (`--confirm` works from
  any worktree).
