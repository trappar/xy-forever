# Work queue and sessions

## The orchestrator

**Reads:** the queue (`python3 scripts/plan.py`: one line per issue), the issue it takes
(`gh issue view <N>`), the local `docs/status/README.md` (its leaves only when needed), this file, agent
reports, and the last lines of check commands. Decision records only when writing a brief that depends on one.
**When delegating, never opens:** source, listings, logs, device reports, HISTORY.md, other docs.
**Writes while coordinating:** issues (comments, labels, new items), the local `docs/status/`, briefs. **Runs:** check
commands and device scripts (the only agent that touches the device or asks the developer for anything).
Without delegation, switch to the scoped worker role described in [Agents](agents.md) for implementation.

### One sitting

1. Run `python3 scripts/plan.py`; read your local `docs/status/README.md`. No local setup yet:
   [ONBOARDING.md](../../ONBOARDING.md) first.
2. Is the device there? `python3 xyf_host.py --timeout 5 status`; if yes, ready `kind:device` items go
   first. If the developer said the device is absent, skip this and every device item.
3. Take the next item: the first under "Yours" (interrupted work), else the first under "Next" (milestone
   order, then issue number; needs are "blocked by" links, so a listed item's needs are done).
4. Read it: `gh issue view <N>` (add `--comments` only when its body says progress is in comments). Claim it:
   `gh issue edit <N> --add-assignee @me`, then `git switch -c <N>-<slug>` from an up-to-date `main`.
5. Write the [brief](agents.md#brief-what-the-orchestrator-sends), then start **one** agent, or do
   the scoped work yourself if delegation is unavailable.
6. After implementation, run the item's check yourself; never accept "tests pass" from a report. Standard:
   `python3 scripts/run_tests.py`; before anything goes to the device: `XYF_EXHAUSTIVE=1` in front.
   - Pass: commit, push the branch, open a pull request (`gh pr create`, body "Closes #<N>": merging
     closes the item), go to 3.
   - Fail: give the agent the commit and failing output; use a fresh agent if context is tight.
     Without delegation, fix within the same scope and rerun. A second failure: label the issue
     `blocked`, comment the reason, unassign it.
7. Stop when a decision is the developer's, when nothing is unblocked, or when told to.
   Close with at most ten lines: done, blocked, waiting on the device, decisions needed.

Progress lives on GitHub (assignee, branch, pull request); an item left unfinished gets a comment saying
where it stands, for the next session. **Asking the developer:** one self-contained
question at a time, your recommendation first (the agent's question tool if any); keep working on what does
not depend on the answer. A question that can wait is a `decision` issue starting "Undecided for now:". All GitHub
text is in the developer's voice ("I", "my explicit yes"), never "the developer" or "the orchestrator".

### Changing the plan

The orchestrator may add, split and reorder items. A new item is an issue that reads alone, per `.github/ISSUE_TEMPLATE/` (Summary, testable Acceptance
criteria, Notes that link); milestone like "M4. Packages"; label `kind:build|research|device|review`
(+ `device:safe|risky` for device work). A gating review or device run is a criterion, not an issue. A need is a "blocked by" link
(`gh api -X POST repos/{owner}/{repo}/issues/<N>/dependencies/blocked_by -F issue_id=<id of the needed issue>`).
A loose end that is not an item yet is an issue labeled `thread`. Items have no IDs of their own: the issue
number is the name. PR titles are plain, like issues; the body says "Closes #<N>". Items marked "to be split" are split by a research agent's decision record,
not by guessing. It may not drop a requirement, change the [shape](../goals/roadmap.md), or relax a safety rule;
those go to the developer. **Device checkpoints first:** prefer the order that reaches a device test soonest;
host-only work never runs far ahead of what the device has confirmed (a byte match proves the output, not that the
device loads it; the simulator guesses whatever was never seen on hardware). Spare agent capacity prepares the next device test.

