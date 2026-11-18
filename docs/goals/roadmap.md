# XY Forever goals: roadmap

Part of [XY Forever goals](../GOALS.md).

## Roadmap

The roadmap is the repository's GitHub issues, in milestones (`python3 scripts/plan.py`); your device's
state is in your local `docs/status/`; current safety lessons are in [SAFETY.md](../SAFETY.md).

### Shape (decided with the developer; the orchestrator may not change it)

One **file engine** (list, read, write, delete, make folder over `/yaffs2`, minus the brick paths in
SAFETY.md), running in our main loops with timeouts. Two doors onto it:

- **Serial protocol** (formal, published spec): commands, installs, automation, the web app.
- **Our own MTP responder**: standard file access with any MTP client.

Firmware, mods and content packs are "put a file, send install" through either door or the on-device menu.
Stock MTP (interrupt code that blocks on processor A) and its watchdog recovery are abandoned; do not continue them.

**Stock code is a driver layer, nothing more.** The stock 1.2.1 bootloader's hardware drivers stay in use
(storage, USB controller, link, screen, keys), each checksum-verified before use. Hard rule:
processor B never waits on processor A without a time limit. We call them as plain functions from our own
loops. We do not run stock features, drive stock state machines, or patch stock state. The [code
rules](../architecture/code-rules.md) define the enforced call boundaries.

## Biggest unknowns

- **Running 1.2.5 on the 1.2.1 bootloader** works (booted from the library); not exercised in depth.
- **Updating the companion chips from XY Forever** (keyboard/backplane 1.0.8,
  Bluetooth 1.0.2) is untested, and it carries the highest risk.
- **How 1.2.5 installs content packs** isn't investigated yet.
- **Writing to storage before the OS starts** works.
- **Self-update: checked offline.** The stock boot-area writer is safe against
  power loss. It can't fall back from a new version that is valid but broken,
  because slot 1 always wins. The current product remains one image; the dormant
  stage-2 loader and any future split are described in [build modes](../architecture/build.md).
- **Per-firmware content:** older firmware such as 1.1.33 boots but is largely
  broken without its own content.
- **A one-time storage scan:** the first mount after some changes can take a
  few seconds; it took 4.6 s on a full scan. This needs a look for boot
  time.
