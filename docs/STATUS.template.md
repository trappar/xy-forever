# XY Forever status

Your own device and session state. Copy this file to `docs/STATUS.md` on first setup;
that copy is local (gitignored) and nobody else reads it. Keep it current whenever a build
is installed or tested, or the device state changes. Work runs from [PLAN.md](PLAN.md)
under [ORCHESTRATION.md](ORCHESTRATION.md); lessons are in [HISTORY.md](HISTORY.md).

Last updated:

## Device now

- Connected or not; where it is (XY Forever menu, keepalive, dev menu). Never leave it in the OS.
- Real bootloader version (must stay 1.2.1, never written).

## Standing approvals

What you granted for this device (ONBOARDING step 6), with its scope: host access, routine USB
installs. Everything else is asked each time.

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

Handover for the next session: what ran, what is waiting on the device, evidence folders.
