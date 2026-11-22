# XY Forever status

Your own device and session state: the index of your status notes, `<private>/xy-forever/status/` (bootstrap
copies `docs/STATUS.template.md` there; outside the repository, nobody else reads it). Only what is true now;
anything longer is a leaf file in this folder, one line under "Leaves". Update it whenever a build is installed
or tested, or the device state changes. Work runs from the GitHub issues (`mise run plan`) under
`docs/ORCHESTRATION.md`; device rules: `docs/SAFETY.md`.

Last updated:

## Device now

- Connected or not; where it is (XY Forever menu, keepalive, dev menu). Never leave it in the OS.
- Real bootloader version (must stay 1.2.1, never written).

## Standing approvals

What you granted for this device (docs/SAFETY.md, "Approvals"), with its scope: host access, routine USB
installs. A specific yes for one item: one line here (date, issue, what exactly). Everything else is asked each time.

## Installed build

Image (`builds/<image>/`, HELLO `image=`), candidate SHA-256, commit, size, protocol version, device pass result.

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

None yet.
