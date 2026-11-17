# Agents and harness capabilities

## Agents

One agent at a time; the only exception: one read-only **research** agent beside one build agent, on
a different item. Never two agents that write.

## Harness capabilities

`AGENTS.md` is the shared entry point. Use the harness's own tools to start a worker, send a follow-up,
and ask a question; no particular tool name or provider is required. Without a question tool, ask in chat.
Without a follow-up tool, include the prior result and remaining task in a fresh brief.

Use a capable coding/reasoning model available in the current harness. Select high reasoning effort
when that control exists; otherwise use the available default. A model name or missing effort setting
must not block work. Tool and sandbox permissions still apply: report a missing required capability
instead of claiming a command ran or bypassing an approval.

**Without sub-agents:** keep the issue, branch, and brief workflow. The main agent may temporarily
act as the research, build, or review worker for that one item, reading only its task's files and making
only its allowed changes. Return to coordination after producing the same report and running the checks.
This is a sequential fallback, never permission for concurrent writers or broader scope. It does not
provide an independent review: if an item requires one, leave that criterion open for another reviewer.
Only the main agent may operate the device or ask the developer for actions; all safety and approval
rules apply in both roles. Read [SAFETY.md](../SAFETY.md) before firmware or device work.

## Worker roles

| Kind | May | Ends with |
|---|---|---|
| research | read anything in the workspace; write only its decision record and its detail leaves | a decision record, <= 8 KB: question, answer, evidence, rejected options |
| build | edit code and tests for its item only | tests pass; one commit; reference docs updated if a fact in them changed |
| review | read the diff and what it touches; write nothing | findings, most serious first, or "none" |

An agent that cannot finish returns "split needed" with a proposed split; it never hands back half-done work or widens its item.
**Context budget**: one concern per item, sized for under 150k tokens or the harness's smaller limit.
When usage is visible, stop before 250k or the harness limit, whichever comes first. Without usage
counters, use a fresh brief per item and hand off when context becomes crowded.
Reading files costs most context, so every brief says: no whole-file reads (ranges of at most 120 lines,
found by `grep -n`), build and test output through `tail`, and it names the functions to read when a previous report gave them.
Follow-ups on the same code go to the same agent while it has room (under 150k when visible),
otherwise to a fresh one with a brief and the previous report.

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

