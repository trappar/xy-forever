# Operating the device: device items

Part of [Operating the device](../OPERATING.md). How the orchestrator runs `device` items
([ORCHESTRATION.md](../ORCHESTRATION.md), "The device"; sub-agents never touch the device).

The rules in [SAFETY.md](../SAFETY.md) are binding for everything here.

- **Scripts state what they need.** A device script first checks the device's
  state. If not, it prints one line (`NEED: power on holding PLAY`) and exits;
  the orchestrator relays that line to the developer and reruns the script.
- **Scripts leave evidence.** Output goes to `../private/xy-forever/sessions/`; the
  script prints a PASS/FAIL table and the folder. On FAIL, investigate from the folder
  (or hand it to a sub-agent), not from pasted log text. A probe it needs is a new script.
- **Checks first, batched.** A device item starts only after its code passed `mise run check` (and the
  simulator, where it covers the change). Ready `device` items wait for a sitting and run together.
- **`device` items** run only main-loop code with timeouts. **`device:risky` items** add
  interrupt code, a new stock call, or new reset behaviour, and run last in a
  sitting, with the developer present.
- **Two strikes.** An item that needs a second manual power cycle stops: it becomes
  `blocked` and a new issue investigates it. Do not iterate on a freezing device.
- Asking for a power cycle: start the listener first, then one short line ([at the device](at-the-device.md)).
