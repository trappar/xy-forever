# Orchestration

The main agent, the **orchestrator**, runs the GitHub issue queue one item at a time, checks results,
and keeps issues current. Delegate implementation when possible; otherwise use the sequential mode
below. Read only what the task needs.

## The orchestrator

**Reads:** the queue (`python3 scripts/plan.py`), its issue (`gh issue view <N>`), local
`docs/status/README.md` (leaves as needed), this guide, agent reports, and check summaries.
Read current reference docs only when the issue needs them. When delegating, leave source, listings,
logs, device reports, and other docs to the worker.
**Writes:** issues, local `docs/status/`, briefs. **Runs:** checks and device scripts.
Without delegation, also do the scoped worker's work below.

### One sitting

1. Run `python3 scripts/plan.py`; read local `docs/status/README.md`. Setup unfinished:
   [ONBOARDING.md](../ONBOARDING.md) first.
2. Check `python3 xyf_host.py --timeout 5 status`; ready `kind:device` items go first when connected.
   If the developer says the device is absent, skip the check and device items.
3. Take the first item under "Yours" (interrupted work), else "Next" (milestone order, then issue
   number). Dependencies are "blocked by" links; listed items have their needs met.
4. Read `gh issue view <N>` (`--comments` only if the body points there). Claim with
   `gh issue edit <N> --add-assignee @me`; branch from up-to-date `main`: `git switch -c <N>-<slug>`.
5. Write the brief below. Start **one** agent, or do its scoped work yourself without delegation.
6. Confirm the check: `python3 scripts/check.py --confirm <branch>` (seconds, no build). A report's
   "tests pass" counts only with a matching receipt, which records the exhaustive suite, the product
   build and `--verify` for that exact tree; do not rerun what it records. Run `python3 scripts/check.py`
   yourself only when the receipt is missing or the tree changed (a rebase or merge onto newer main).
   Merging uses the same receipt. A receipt never stands in for a device test.
   - Pass: push the branch, open a PR (`gh pr create`, body "Closes #<N>"), return to step 3.
   - Fail: give the worker what `--confirm` printed and the log it names; start fresh if context is tight.
     Without delegation, commit and run `scripts/check.py` yourself; fix within scope and rerun.
     A second failure: label `blocked`, comment why, and unassign the issue.
7. Stop when a decision needs the developer, nothing is unblocked, or told to.
   Close in at most ten lines: done, blocked, waiting on the device, decisions needed.

Progress lives on GitHub (assignee, branch, PR). Comment on unfinished items so the next session can
resume. Ask one self-contained question at a time, recommendation first; use the harness's question
tool or chat, and keep working on independent tasks. Deferrable questions become `decision` issues
starting "Undecided for now:". GitHub text uses the developer's voice ("I", "my explicit yes").

### Changing the plan

The orchestrator may add, split, and reorder items. Each issue stands alone and follows
`.github/ISSUE_TEMPLATE/`: Summary, testable Acceptance criteria, linked Notes; a milestone like
"M4. Packages"; `kind:build|research|device|review` (+ `device:safe|risky` for device work).
A gating review or device run is a criterion, not an issue. Dependencies use "blocked by" links:
`gh api -X POST repos/{owner}/{repo}/issues/<N>/dependencies/blocked_by -F issue_id=<needed issue id>`.
Findings made while working an item are fixed in its branch as extra commits and listed in its PR body. A new
issue only when the fix would become the PR's main scope or nothing in progress relates, and then the most
related open issue before a new one. `thread` is for findings with no related open work.
Issue numbers identify items; PR titles are plain and bodies say "Closes #<N>".
Items marked "to be split" need findings and a proposed split in their GitHub issue first.

Dropping requirements, changing the [shape](goals/roadmap.md), or relaxing safety rules needs the
developer. Prefer work that reaches a device test soonest; host work must not run far ahead of
hardware evidence. Byte matches and simulation cannot prove a device loads a build. Spare agent
capacity prepares the next device test.

## Agents

One worker at a time, except one read-only research agent may accompany a build agent on another
item. Never two writers. Use a capable model available in the harness, with high reasoning effort
if configurable; otherwise use its default. No provider or tool names are required. Use native
worker/follow-up tools; without follow-ups, send a fresh brief with the prior report.

**Without sub-agents:** keep the same issue, branch, brief, role limits, report, and checks. The main
agent may read and edit its item's files, then return to coordination. Self-review does not satisfy
an independent-review requirement; leave that criterion open. Safety, permissions, and approvals
apply in either mode. Report missing required capabilities; never bypass them.

| Kind | May | Ends with |
|---|---|---|
| research | read workspace; report findings in its GitHub issue | concise answer, evidence links, uncertainty and actionable follow-ups |
| build | edit its item's code and tests | as many commits as the work needs, then one `python3 scripts/check.py` on the last (a passing receipt); reference docs updated for changed facts |
| review | read the diff and what it touches; write nothing | findings by severity, or "none" |

Absorb small findings. If an item is too large, or a finding would take over the item, return "split needed"
with a proposed split; never hand back half-done work. Aim below 150k tokens or the harness's smaller limit; stop before 250k or its limit.
Without counters, use a fresh brief per item and hand off when context crowds. Reuse a worker for
follow-ups while it has room; otherwise send its report to a fresh one. Read ranges of at most
120 lines found with `rg -n`; pipe build/test output through `tail`. Name functions when known.

### Brief (what the orchestrator sends)

```
Issue #<N>: <title>. Kind: <kind>.
Goal: <acceptance criteria>.
Read first: xy-forever/AGENTS.md, then <at most five current reference files>.
Do not read: docs/status/, unrelated docs.
Constraints: <safety notes; required explicit approvals in the developer's words>.
Check: commit as often as needed, then end with one `python3 scripts/check.py` <+ --neutral for a behaviour-neutral change>;
the orchestrator confirms its receipt (`--confirm`), so a report's "tests pass" needs one.
Report: <= 15 lines: changed files/functions with line numbers, checks, next-item facts,
open questions, three largest context costs. No narrative.
```

## Where things go

| Thing | Place |
|---|---|
| What to do next, loose ends, questions for the developer | GitHub issues: items, `thread`, `decision` (`scripts/plan.py`) |
| Device state, installed build, open device problems, session notes | your local `docs/status/README.md` (gitignored; detail in leaf files beside it) |
| Research findings, decisions, rejected options, evidence and follow-ups | the relevant GitHub issue; link existing issues rather than copy them |
| Current facts developers need (addresses, formats, design, safety) | the reference doc or code comment that owns them; concise, checked against the implementation |
| What a test showed | your local `docs/status/`; a lasting current rule in its owning reference |
| Logs, dumps, probe output, screenshots, scratch scripts | `../private/xy-forever/sessions/<date>-<topic>/`, never the repository |
| What an agent tried that did not work | its report and the relevant GitHub issue if it prevents a repeat |

Do not create research reports, numbered decision records, ADRs, or archive indexes in the source tree.
Git history holds how the implementation arrived here; current references explain what developers need now.
No other new files in `docs/`. `tests/test_docs_budget.py` budgets docs in bytes and line width, checks
their links, and fails the suite when one grows past it: split it into a tree (an index plus leaves under 8 KB each), never squeeze.

## The device

Aim: one power-on per sitting, and none for offline work.

- **Sub-agents never touch the device.** They cannot ask the developer to power cycle it or
  put it in a mode. A build agent writes the device script for its item and proves it
  against the simulator. The orchestrator runs that script for a `device` item.
- **No guard:** there is no watchdog; a hang needs a manual power cycle. Never send REBOOT after a
  suspected lone A restart. Stop on a suspected hang and request one ([SAFETY.md](SAFETY.md)).
  Sessions end in keepalive, never in the OS.
- **Running device items** (NEED lines, evidence, batching, `safe` and `risky`, two strikes):
  [operating/device-items.md](operating/device-items.md).

## Approvals

Approvals belong to one developer and their device. Needs their explicit yes, named in the brief:
relaxing a builder audit (call allowlist, CRC spans, symbol rules), a new kind of storage write,
companion chips, 1.2.5, anything SAFETY.md lists; every allowlist or span change gets a **review**
item first. Standing approvals (ONBOARDING step 6, in the local `docs/status/README.md`; specific ones in its `approvals/`) cover routine work only,
such as a USB install of a verified build with one understood change, said first.
