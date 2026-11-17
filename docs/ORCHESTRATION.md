# Orchestration

How work on XY Forever is run. The session's main agent, the **orchestrator**, runs the GitHub
issue queue one item at a time, checks results, and keeps the issues current. Use sub-agents when
available; otherwise follow the sequential workflow below. Context is the cost: read the smallest
thing that answers the question.

Read both before taking an item:

- [Work queue and sessions](orchestration/workflow.md): claiming issues, branches, checks, and progress.
- [Agents and harness capabilities](orchestration/agents.md): delegation, sequential work, models, and briefs.

Before device work, also read [Device sessions](orchestration/device.md) and [SAFETY.md](SAFETY.md).
The approvals below apply in every harness.

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

## Approvals

Approvals belong to one developer and their device. Needs their explicit yes, named in the brief:
relaxing a builder audit (call allowlist, CRC spans, symbol rules), a new kind of storage write,
companion chips, 1.2.5, anything SAFETY.md lists; every allowlist or span change gets a **review**
item first. Standing approvals (ONBOARDING step 6, in the local `docs/status/README.md`; specific ones in its `approvals/`) cover routine work only,
such as a USB install of a verified build with one understood change, said first.
