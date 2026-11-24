# Orchestration

The main agent, the **orchestrator**, works the GitHub issue queue one item at a time and keeps issues
current. With sub-agents, it never reads or edits source, tests or build scripts: it delegates every implementation
and every question about how the code works (the developer's "why does X happen?" too) and relays the answer.
It reads issues, PRs, CI logs, plan output and the status notes. Its jobs: issues, briefs, confirming check
receipts (`mise run check --confirm`), pushing, device scripts, status notes, telling the developer what is
ready. Only a session with no sub-agents at all works items itself, in sequence. Read only what the task needs.

## One item

1. `mise run plan` (another agent works here too: [two agents](operating/two-agents.md)); read
   your status notes (`<private>/xy-forever/status/README.md`). Setup unfinished: [ONBOARDING.md](../ONBOARDING.md) first.
2. `uv run xyf-host --timeout 5 status`: ready `device` items go first when the device is connected.
   If the developer says it is absent, or there was no [first device install](operating/first-install.md) yet,
   skip the check and device items.
3. Resume interrupted work (its branch or PR), else take the first item under "Next" (milestone order, then
   issue number).
4. Read `gh issue view <N>`. With sub-agents, a worker does the branch, commits and checks (4–6) from your brief.
   **Branch** from up-to-date `main` in its own worktree ([two agents](operating/two-agents.md)).
5. Work in small commits; after each change, `mise run test-changed` ([checks](operating/checks.md)).
6. Once per PR, `mise run check`. Then **PR**: push, `gh pr create`, body "Closes #<N>".
7. **CI green** (`gh pr checks <n> --watch`) is the merge check; fix failures in the branch (a worker's job, with
   sub-agents). A check never stands in for a device test.
8. Stop when a decision needs the developer, nothing is unblocked, or told to.

Progress lives on GitHub (branch, PR). Comment on unfinished items so the next session can resume.
Keep working on independent tasks while a question waits; a deferrable one becomes a `decision` issue.
GitHub text uses the developer's voice ("I", "my"). A sub-agent gets the issue, what to read, its
constraints and the report wanted.

## Changing the plan

Each issue stands alone and follows `.github/ISSUE_TEMPLATE/`: Summary, testable Acceptance criteria, linked
Notes; the milestone it plainly belongs to (none is fine for a loose end). Labels, only when they apply:
`device` (needs the OP-XY), `device:risky` (adds interrupt code, a new stock call or new reset behaviour),
`blocked` (reason in the last comment), `thread` (a loose end, not yet an item), `decision`. Dependencies use "blocked by" links:
`gh api -X POST repos/{owner}/{repo}/issues/<N>/dependencies/blocked_by -F issue_id=<needed issue id>`.
Findings made while working an item are fixed in its branch and listed in its PR body; a new issue only when
the fix would take over the PR. `thread` is for findings with no related open work. PR titles are plain.
Dropping requirements, changing the [shape](goals/roadmap.md), or relaxing safety rules needs the developer.
Prefer work that reaches a device test soonest: byte matches and simulation cannot prove a device loads a build.

## Where things go

| Thing | Place |
|---|---|
| What to do next, loose ends, questions for the developer | GitHub issues: items, `thread`, `decision` (`mise run plan`) |
| Device state, installed build, open device problems, session notes, approvals | your status notes: `<private>/xy-forever/status/README.md` (outside the repository; detail in leaf files beside it) |
| Research findings, decisions, rejected options, evidence and follow-ups | the relevant GitHub issue |
| Current facts developers need (addresses, formats, design, safety) | the reference doc or code comment that owns them |
| Logs, dumps, probe output, screenshots, scratch scripts | `../private/xy-forever/sessions/<date>-<topic>/`, never the repository |

A new doc is fine when it holds current reference facts; findings and history stay in the issue.
`tests/test_docs_budget.py` budgets docs in bytes and checks their links: a doc past its budget is split into a
tree (an index plus leaves under 8 KB each), never squeezed.

## The device

- **One agent talks to the device at a time.** Any agent can take the [session lease](operating/two-agents.md#device-lease).
- **`device:risky` items run only with the developer present.**

Everything else about device work: [SAFETY.md](SAFETY.md) (binding) and [OPERATING.md](OPERATING.md).
