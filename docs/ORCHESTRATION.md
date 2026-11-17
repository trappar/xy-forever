# Orchestration

How work on XY Forever is run. The session's main agent, the **orchestrator**, reads and writes no
code: it runs the plan, which is the repository's GitHub issues, one item at a time through sub-agents,
checks their results, and keeps the issues current. Context is the cost (open-ended sessions bloat
contexts, cost power cycles; parallel agents collide): read the smallest thing that answers the question.

## The orchestrator

**Reads:** the queue (`python3 scripts/plan.py`: one line per issue), the issue it takes
(`gh issue view <N>`), the local `docs/status/README.md` (its leaves only when needed), this file, agent
reports, and the last lines of check commands. Decision records only when writing a brief that depends on one.
**Never opens:** source, listings, logs, device reports, HISTORY.md, other docs.
**Writes:** issues (comments, labels, new items), the local `docs/status/`, briefs. Nothing else. **Runs:** check
commands and device scripts (the only agent that touches the device or asks the developer for anything).

### One sitting

1. Run `python3 scripts/plan.py`; read your local `docs/status/README.md`. No local setup yet:
   [ONBOARDING.md](../ONBOARDING.md) first.
2. Is the device there? `python3 xyf_host.py --timeout 5 status`; if yes, ready `kind:device` items go
   first. If the developer said the device is absent, skip this and every device item.
3. Take the next item: the first under "Yours" (interrupted work), else the first under "Next" (milestone
   order, then issue number; needs are "blocked by" links, so a listed item's needs are done).
4. Read it: `gh issue view <N>` (add `--comments` only when its body says progress is in comments). Claim it:
   `gh issue edit <N> --add-assignee @me`, then `git switch -c <N>-<slug>` from an up-to-date `main`.
5. Write the brief (below), start **one** agent.
6. When it returns, run the item's check yourself; never accept "tests pass" from a report. Standard:
   `python3 scripts/run_tests.py`; before anything goes to the device: `XYF_EXHAUSTIVE=1` in front.
   - Pass: commit, push the branch, open a pull request (`gh pr create`, body "Closes #<N>": merging
     closes the item), go to 3.
   - Fail: give a fresh agent the commit and the failing output (the same agent only if it is under
     200k tokens). A second failure: label the issue `blocked`, comment the reason, unassign it.
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
not by guessing. It may not drop a requirement, change the [shape](goals/roadmap.md), or relax a safety rule;
those go to the developer. **Device checkpoints first:** prefer the order that reaches a device test soonest;
host-only work never runs far ahead of what the device has confirmed (a byte match proves the output, not that the
device loads it; the simulator guesses whatever was never seen on hardware). Spare agent capacity prepares the next device test.

## Agents

One agent at a time; the only exception: one read-only **research** agent beside one build agent, on
a different item. Never two agents that write. Every sub-agent runs on **Opus 5.5 with high
reasoning** (`model: "opus"`, `effort: "high"`), whatever the orchestrator's own model is.

| Kind | May | Ends with |
|---|---|---|
| research | read anything in the workspace; write only its decision record and its detail leaves | a decision record, <= 8 KB: question, answer, evidence, rejected options |
| build | edit code and tests for its item only | tests pass; one commit; reference docs updated if a fact in them changed |
| review | read the diff and what it touches; write nothing | findings, most serious first, or "none" |

An agent that cannot finish returns "split needed" with a proposed split; it never hands back half-done work or widens its item.
**Context budget**: one concern per item, sized for under 150k tokens (every call re-sends the context); past 250k stop the agent.
Reading files costs most context, so every brief says: no whole-file reads (ranges of at most 120 lines,
found by `grep -n`), build and test output through `tail`, and it names the functions to read when a previous report gave them.
Follow-ups on the same code go to the same agent while it is under 150k (SendMessage), otherwise to a fresh one.

### Brief (what the orchestrator sends)

```
Issue #<N>: <title>. Kind: <kind>.
Goal: <the acceptance criteria>.
Read first: xy-forever/AGENTS.md, then <at most five named files or decision records>.
Do not read: docs/status/, HISTORY.md, unrelated docs.
Constraints: <safety notes; the developer's explicit yes where needed, in their words>.
Check: <command(s) the orchestrator will run>.
Report: <= 15 lines: what changed (files, functions with line numbers), check result, what the next
item must know, open questions, the three things that used most of your context. No narrative.
```

## Where things go

| Thing | Place |
|---|---|
| What to do next, loose ends, questions for the developer | GitHub issues: items, `thread`, `decision` (`scripts/plan.py`) |
| Device state, installed build, open device problems, session notes | your local `docs/status/README.md` (gitignored; detail in leaf files beside it) |
| A settled question (so nobody researches it twice) | `docs/decisions/NNN-title.md`; detail in `NNN-title.<topic>.md` beside it, linked from it |
| Facts that stay true (addresses, formats, design) | the reference doc that owns them |
| What a test showed | your local `docs/status/`; a lasting lesson (what failed, the rule it produced) as one line in HISTORY.md |
| Logs, dumps, probe output, screenshots, scratch scripts | `../private/xy-forever/sessions/<date>-<topic>/`, never the repository |
| What an agent tried that did not work | its report; one line in the decision record if it prevents a repeat |

No other new files in `docs/`. `tests/test_docs_budget.py` budgets docs in bytes and line width, checks
their links, and fails the suite when one grows past it: split it into a tree (an index plus leaves under 8 KB each), never squeeze.

## The device

Aim: one power-on per sitting, and none for offline work.

- **Sub-agents never touch the device.** They cannot ask the developer to power cycle it or
  put it in a mode. A build agent writes the device script for its item and proves it
  against the simulator. The orchestrator runs that script for a `device` item.
- **Scripts state what they need.** A device script first checks the device's
  state. If not, it prints one line (`NEED: power on holding PLAY`) and exits;
  the orchestrator relays that line to the developer and reruns the script.
- **Scripts leave evidence.** Output goes to `../private/xy-forever/sessions/`; the
  script prints a PASS/FAIL table and the folder. On FAIL the orchestrator gives a
  research agent the folder, not the log text. A probe it needs is a new script.
- **Simulator first, batched.** A device item starts only after its code passes the simulator and
  the offline suite. Ready `kind:device` items wait for a sitting and run together.
- **Guard on** for every device session, so a hang resets the device and the service
  comes back. Sessions end in keepalive, never in the OS.
- **`safe` items** run only main-loop code with timeouts. **`risky` items** add
  interrupt code, a new stock call, or new reset behaviour, and run last in a
  sitting, with the developer present.
- **Two strikes.** An item that needs a second manual power cycle stops: it becomes
  `blocked` and a research item is added. Do not iterate on a freezing device.
- Asking for a power cycle: start the listener first, then one short line (operating/at-the-device.md).

## Approvals

Approvals belong to one developer and their device. Needs their explicit yes, named in the brief:
relaxing a builder audit (call allowlist, CRC spans, symbol rules), a new kind of storage write,
companion chips, 1.2.5, anything SAFETY.md lists; every allowlist or span change gets a **review**
item first. Standing approvals (ONBOARDING step 6, in the local `docs/status/README.md`; specific ones in its `approvals/`) cover routine work only,
such as a USB install of a verified build with one understood change, said first.
