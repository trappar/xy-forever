# XY Forever status

Your own device and session state: the index of your local, gitignored `docs/status/` folder (bootstrap copies
`docs/STATUS.template.md` here); nobody else reads it. It holds only what is true now, under 6 KB (checked by
`tests/test_docs_budget.py`); anything longer is a leaf file in this folder, one line under "Leaves". Keep it
current whenever a build is installed or tested, or the device state changes. Work runs from the GitHub
issues (`scripts/plan.py`) under [ORCHESTRATION.md](../ORCHESTRATION.md); lessons: [SAFETY.md](../SAFETY.md).

Last updated:

## Device now

- Connected or not; where it is (XY Forever menu, keepalive, dev menu). Never leave it in the OS.
- Real bootloader version (must stay 1.2.1, never written).

## Standing approvals

What you granted for this device (ONBOARDING step 6), with its scope: host access, routine USB installs.
A specific yes for one item: `approvals/<date>-<topic>.md`, one file each, one line each in
`approvals/README.md`. Everything else is asked each time.

## Installed build

Build number, output folder, candidate SHA-256, commit, size, protocol version, device pass result.

## Known-good build and recovery kit

The last build that passed a full device pass, and where its dev-menu kit (`os.ldr`) is.

## Library and files on the device (`/yaffs2/xy-forever/`)

| Entry | What |
|---|---|

## Open device problems

None recorded.

## Session notes

Handover for the next session: what ran, what is waiting on the device, evidence folders. Short; an older
note moves to a leaf or goes.

## Leaves

- `approvals/`: specific approvals, one file each (index: its README.md).
