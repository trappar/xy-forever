# XY Forever: goals

XY Forever is a firmware and mod manager that runs before the OP-XY
software. Once it is installed, the user never needs the stock bootloader or
the dev menu again. Trying out firmware and mods should be easy.

It supports OP-XYs on 1.2.1 only: it is installed from the 1.2.1 bootloader's dev menu,
and a device updated to 1.2.5 cannot be downgraded with the built-in firmware updater.

## Contents

- [Features](goals/features.md): the features: normal startup, UI, firmware library, USB file access, official updates, mods, update manager, uninstall, safety
- [How it works](goals/how-it-works.md): what stays and what changes, no embedded OS, the two processors, stock code as a driver layer, booting, updates, mods
- [Roadmap](goals/roadmap.md): the roadmap; the shape (decided with the developer; the orchestrator may not change it); the biggest unknowns
- [Decisions](goals/decisions.md): the decisions: the name, the 1.2.1 bootloader, no embedded OS, the protocol, mods and mod trust, content,
  packages, what is not planned
