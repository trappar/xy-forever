# Orchestration

The main agent, the **orchestrator**, works the GitHub issue queue one item at a time and keeps issues
current. It delegates implementation when it can, or does the work itself. Read only what the task needs.

## One item

1. `mise run plan` (`-- --agent NAME` if another agent works here: [two agents](operating/two-agents.md)); read
   local `docs/status/README.md`. Setup unfinished: [ONBOARDING.md](../ONBOARDING.md) first.
2. `uv run xyf_host.py --timeout 5 status`: ready `kind:device` items go first when the device is connected.
   If the developer says it is absent, skip the check and device items.
3. Take the first item under "Yours" (interrupted work), else "Next" (milestone order, then issue number).
4. **Claim** it: read `gh issue view <N>`; `gh issue edit <N> --add-assignee @me` (with `--agent`, also
   `--add-label agent:NAME`). **Branch** from up-to-date `main`: `git switch -c <N>-<slug>`.
5. Work in small commits. After each change run the tests it affects (`mise run test-changed`, or
   `mise run test-one TARGET`); `mise run test` (the fast tier) at any time.
6. Once per PR, `mise run check` (the PR gate: build, verify, type checks, the fast and gate tiers, under
   30 s; [checks](operating/build-install.md#checks)). Then **PR**: push, `gh pr create`, body "Closes #<N>".
7. **CI green** (`gh pr checks <n> --watch`) is the merge check; fix failures in the branch. A check never
   stands in for a device test.
8. Stop when a decision needs the developer, nothing is unblocked, or told to. Close in at most ten lines:
   done, blocked, waiting on the device, decisions needed.

Progress lives on GitHub (assignee, branch, PR). Comment on unfinished items so the next session can resume.
Ask one self-contained question at a time, recommendation first, and keep working on independent tasks.
Deferrable questions become `decision` issues starting "Undecided for now:". GitHub text uses the
developer's voice ("I", "my explicit yes"). A sub-agent gets the issue, what to read, its constraints and
the report wanted; it never touches the device.

## Changing the plan

Each issue stands alone and follows `.github/ISSUE_TEMPLATE/`: Summary, testable Acceptance criteria, linked
Notes; a milestone like "M4. Packages"; `kind:build|research|device|review` (+ `device:safe|risky`).
Dependencies use "blocked by" links:
`gh api -X POST repos/{owner}/{repo}/issues/<N>/dependencies/blocked_by -F issue_id=<needed issue id>`.
Findings made while working an item are fixed in its branch and listed in its PR body; a new issue only when
the fix would take over the PR. `thread` is for findings with no related open work. PR titles are plain.
Dropping requirements, changing the [shape](goals/roadmap.md), or relaxing safety rules needs the developer.
Prefer work that reaches a device test soonest: byte matches and simulation cannot prove a device loads a build.

## Where things go

| Thing | Place |
|---|---|
| What to do next, loose ends, questions for the developer | GitHub issues: items, `thread`, `decision` (`mise run plan`) |
| Device state, installed build, open device problems, session notes | your local `docs/status/README.md` (gitignored; detail in leaf files beside it) |
| Research findings, decisions, rejected options, evidence and follow-ups | the relevant GitHub issue |
| Current facts developers need (addresses, formats, design, safety) | the reference doc or code comment that owns them |
| Logs, dumps, probe output, screenshots, scratch scripts | `../private/xy-forever/sessions/<date>-<topic>/`, never the repository |

No research reports, numbered decision records, ADRs or archive indexes in the source tree, and no other new
files in `docs/`. `tests/test_docs_budget.py` budgets docs in bytes and checks their links: a doc past its
budget is split into a tree (an index plus leaves under 8 KB each), never squeezed.

## The device

Aim: one power-on per sitting, and none for offline work.

- **Sub-agents never touch the device.** They cannot ask the developer to power cycle it or put it in a mode.
  A build agent writes the device script for its item and proves it against the simulator; the orchestrator
  runs it for a `device` item.
- **No guard:** there is no watchdog; a hang needs a manual power cycle. Never send REBOOT after a suspected
  lone A restart. Stop on a suspected hang and request one ([SAFETY.md](SAFETY.md)). Sessions end in
  keepalive, never in the OS.
- **Running device items** (NEED lines, evidence, batching, `safe` and `risky`, two strikes):
  [operating/device-items.md](operating/device-items.md).

## Approvals

Approvals belong to one developer and their device. Needs their explicit yes: relaxing a builder audit (call
allowlist, CRC spans, symbol rules), a new kind of storage write, companion chips, 1.2.5, anything SAFETY.md
lists; every allowlist or span change gets a **review** item first. Standing approvals (ONBOARDING step 6, in
the local `docs/status/README.md`; specific ones in its `approvals/`) cover routine work only, such as a USB
install of a verified build with one understood change, said first.
