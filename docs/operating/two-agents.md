# Two agents at once

Two agents may work this repository on one machine at the same time. They cannot message each other, so the
branch and the working tree say who has what.

- **One worktree per agent and item**, outside the workspace folder (the one holding this checkout and `private/`): from the main checkout, `git worktree add ../../<workspace>-worktrees/<N> -b <N>-<slug> origin/main`.
  A worktree inside the workspace is watched by an editor opened on it (VS Code's Vitest extension starts a
  watcher for each). Two agents never share a working tree. Both agents' branches and PRs are under one GitHub
  account: resume only the item whose worktree you are in; an item with a worktree is someone's.
- A worktree, wherever it is, finds the same private folder (status notes included) and
  `xyf.local.json` as the main checkout (`main_checkout` in `xy_forever/paths.py`). Its `.venv` and
  `node_modules` are its own: `mise run setup` there before checks. Never `git stash`: the stash is shared.
- `mise run check` runs side by side: pruning (`scripts/clean.py`) skips build folders touched in the last
  hour and keeps each worktree's newest verified build, and receipts are keyed by tree (`--confirm` works from
  any worktree).

## Device lease

Any agent can reserve the OP-XY for a whole session, including the gaps between install, probes and
MTP commands. Run a foreground script under the lease from the checkout. The script has no interactive
input. Follow [device safety](../SAFETY.md) and the [device rules](../ORCHESTRATION.md#the-device).

```sh
uv run -m xy_forever.device_session --name readback --timeout 120 -- \
  sh -ec 'xyf-host --timeout 5 status; xyf-host --timeout 5 space'
```

- A busy device names the holder, PID and start time. `--wait SECONDS` bounds the wait to take a lease
  (default: fail immediately). Standalone host tools still take the same OS lock for their command.
- `--timeout SECONDS` bounds the whole session (default: 900). Size it for the longest expected run,
  including any install and read-back. Expiry ends the commands and returns 124. Exiting or losing the
  lease holder releases the lease and ends its command group. Within the lease, the host tools
  still serialize access so only one physical command runs at a time.
- Device passes inside a lease leave keepalive to the session owner. To poll at the end of a lease,
  run `sh scripts/keepalive.sh STATEDIR` as its final foreground command. Use the stop file to end the
  loop. For keepalive after a lease ends, start it separately. Standalone passes hand off automatically.
- The lock coordinates XY Forever's host tools across worktrees, including the automated Chrome
  test. Coordinate manual tools with the holder before opening the device. The simulator takes no lock.
