# Device sessions

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

