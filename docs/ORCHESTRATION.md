# Orchestration

How work on XY Forever is run. The session's main agent, the **orchestrator**, reads and writes no
code: it runs [PLAN.md](PLAN.md) one item at a time through sub-agents, checks their results, and keeps
the plan current. Budget: 125 lines (open-ended sessions bloat contexts, cost power cycles; parallel agents collide).

## The orchestrator

**Reads:** PLAN.md, the local STATUS.md, this file, agent reports, and the last lines of check
commands. Decision records only when writing a brief that depends on one.
**Never opens:** source, listings, logs, device reports, HISTORY.md, other docs.
**Writes:** PLAN.md, the local STATUS.md, briefs. Nothing else. **Runs:** check commands and device
scripts (the only agent that touches the device or asks the developer for anything).

### One sitting

1. Read PLAN.md and your local `docs/STATUS.md`. No local setup yet: [ONBOARDING.md](../ONBOARDING.md) first.
2. Is the device there? `python3 xyf_host.py --timeout 5 status`; if yes, the device queue goes
   first. If the developer said the device is absent, skip this and the device queue.
3. Take the next item: first one whose GitHub issue is assigned to you (your interrupted work;
   without a remote: a `[~]` you set yourself this sitting), else the first `[ ]` item whose needs
   are done, honouring any pause PLAN.md states. Skip device items when the device is absent.
4. Claim it: find its issue (`gh issue list --search "<ID>"`). Assigned to someone else: skip
   it. Otherwise `gh issue edit <N> --add-assignee @me`, then `git switch -c <ID>-<slug>` from an
   up-to-date `main`. (No GitHub remote yet: skip the claim, work on `main`.)
5. Mark it `[~]`, write the brief (below), start **one** agent.
6. When it returns, run the item's check yourself; never accept "tests pass" from a report. Standard:
   `python3 scripts/run_tests.py`; before anything goes to the device: `XYF_EXHAUSTIVE=1` in front.
   - Pass: mark `[x]`, commit, push the branch, open a pull request (`gh pr create`; no remote: commit
     on `main` only), go to 3.
   - Fail: give a fresh agent the commit and the failing output (the same agent only
     if it is under 200k tokens). A second failure marks the item `[!]` with a reason.
7. Stop when a decision is the developer's, when nothing is unblocked, or when told to.
   Close with at most ten lines: done, blocked, waiting on the device, decisions needed.

The plan is committed with every item, so the next session resumes from disk. **Asking the developer:**
one self-contained question at a time, your recommendation first (the agent's question tool if any);
keep working on what does not depend on the answer.

### Changing the plan

The orchestrator may add, split and reorder items. Items marked "to be split" are split by a
research agent's decision record, not by guessing. It may not drop a requirement, change the shape,
or relax a safety rule; those go to the developer. **Device checkpoints first:** prefer the order
that reaches a device test soonest; host-only work never runs far ahead of what the device has
confirmed (a byte match proves the output, not that the device loads it; the simulator guesses
whatever was never seen on hardware). Spare agent capacity prepares the next device test.

## Agents

One agent at a time; the only exception: one read-only **research** agent beside one build agent, on
a different item. Never two agents that write. Every sub-agent runs on **Opus 5.5 with high
reasoning** (`model: "opus"`, `effort: "high"`), whatever the orchestrator's own model is.

| Kind | May | Ends with |
|---|---|---|
| research | read anything in the workspace; write only its decision record | `docs/decisions/NNN-title.md`, <= 60 lines: question, answer, evidence (addresses, files), rejected options |
| build | edit code and tests for its item only | tests pass; one commit; reference docs updated if a fact in them changed |
| review | read the diff and what it touches; write nothing | findings, most serious first, or "none" |

An agent that cannot finish returns "split needed" with a proposed split; it never hands back half-done work or widens its item.
**Context budget**: one concern per item, sized for under 150k tokens (every call re-sends the context); past 250k stop the agent.
Most of an agent's context goes to reading files, so every brief says: no whole-file reads (ranges of at most 120 lines,
found by `grep -n`), build and test output through `tail`, and it names the functions to read when a previous report gave them.
Follow-ups on the same code go to the same agent while it is under 150k (SendMessage), otherwise to a fresh one.

### Brief (what the orchestrator sends)

```
Item <ID>: <title>. Kind: <kind>.
Goal: <the "done when" text>.
Read first: xy-forever/AGENTS.md, then <at most five named files or decision records>.
Do not read: STATUS.md history, HISTORY.md, unrelated docs.
Constraints: <safety notes; the developer's explicit yes where needed, in their words>.
Check: <command(s) the orchestrator will run>.
Report: <= 15 lines: what changed (files, functions with line numbers), check result, what the next
item must know, open questions, the three things that used most of your context. No narrative.
```

## Where things go

| Thing | Place |
|---|---|
| What to do next | PLAN.md |
| Device state, installed build, open device problems, session notes | your local `docs/STATUS.md` (gitignored; copy `STATUS.template.md` on first setup) |
| A settled question (so nobody researches it twice) | `docs/decisions/NNN-title.md` |
| Facts that stay true (addresses, formats, design) | the reference doc that owns them |
| What a test showed | your local STATUS.md; a lasting lesson (what failed, the rule it produced) as one line in HISTORY.md |
| Logs, dumps, probe output, screenshots, scratch scripts | `../private/xy-forever/sessions/<date>-<topic>/`, never the repository |
| What an agent tried that did not work | its report; one line in the decision record if it prevents a repeat |

No other new files in `docs/`. `tests/test_docs_budget.py` fails the suite when a budgeted doc
(PLAN, STATUS.template, AGENTS, ONBOARDING, this file, a decision record, a reference doc) grows past its budget.

## The device

The aim is one power-on per sitting, and none for offline work.

- **Sub-agents never touch the device.** They cannot ask the developer to power cycle it or
  put it in a mode. A build agent writes the device script for its item and proves it
  against the simulator. The orchestrator runs that script for a `device` item.
- **Scripts state what they need.** A device script first checks the device is in the
  state it needs. If not, it prints one line (`NEED: power on holding PLAY`) and exits;
  the orchestrator relays that line to the developer and reruns the script.
- **Scripts leave evidence.** Output goes to `../private/xy-forever/sessions/`; the
  script prints a PASS/FAIL table and the folder. On FAIL the orchestrator gives a
  research agent the folder, not the log text. A probe it needs is a new script.
- **Simulator first, batched.** A device item starts only after its code passes the simulator and
  the offline suite. Device items wait in the plan's device queue and run together.
- **Guard on** for every device session, so a hang resets the device and the service
  comes back. Sessions end in keepalive, never in the OS.
- **`safe` items** run only main-loop code with timeouts. **`risky` items** add
  interrupt code, a new stock call, or new reset behaviour. They run last in a
  sitting, with the developer present.
- **Two strikes.** If an item needs a second manual power cycle, it stops. It becomes
  `[!]` and a research item is added. Do not iterate on a freezing device.
- Asking for a power cycle: start the listener first, then one short line (OPERATING.md).

## Approvals

Approvals belong to one developer and their device. Needs their explicit yes, named in the brief:
relaxing a builder audit (call allowlist, CRC spans, symbol rules), a new kind of storage write,
companion chips, 1.2.5, anything SAFETY.md lists; every allowlist or span change gets a **review**
item first. Standing approvals (ONBOARDING step 6, in the local STATUS.md) cover routine work only,
such as a USB install of a verified build with one understood change, said first.
