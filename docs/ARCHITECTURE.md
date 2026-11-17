# XY Forever architecture

How XY Forever boots, how its two halves talk, and where the code is. Product
goals: [GOALS.md](GOALS.md). On-device files: [STORAGE.md](STORAGE.md). Addresses
and stock-code facts: [HARDWARE.md](HARDWARE.md). Roadmap: GitHub issues.

Status marks: **proven** (ran on the device), **built** (offline, verified, not
device-tested), **planned**.

## Contents

- [Boot](architecture/boot.md): the boot chain, the memory map (product, build 171), the processors, boot flows, boot model v3, writes
- [The link between the processors](architecture/link.md): the stock SPI request protocol, reopened at cold time
- [Simulator](architecture/simulator.md): the offline simulator
- [Self-update and build modes](architecture/build.md): self-update and uninstall, build modes (`xyf_build.py`), feature switches
- [Code map](architecture/code-map.md): where the code lives; mods (open-xy): offline findings
- [Safety rules](architecture/code-rules.md): the rules for all code
