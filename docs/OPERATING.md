# Operating the device

How to build, install and test XY Forever on the developer's OP-XY, and how to work
with the developer at the device. The rules in [SAFETY.md](SAFETY.md) are binding for everything here.

## Contents

- [First device install](operating/first-install.md): stock files off the OP-XY, approvals for this device, the first install through the dev menu, the first device pass
- [Build, verify, install](operating/build-install.md): build, verify and install XY Forever, every build; checks: tests, tiers, the PR gate (`mise run check`)
- [Device pass and mod boot](operating/device-pass.md): `scripts/device_pass.py` and `scripts/mod_boot.py`: stages, flags, NEED/exit 3, recovery
- [Host tool](operating/host-tool.md): `uv run xyf-host` and its file commands
- [At the device](operating/at-the-device.md): taking control of the device, timing, reports, the dev menu, working with the developer at the device
- [Device items](operating/device-items.md): how the orchestrator runs `device` items: NEED lines, evidence, batching, `device:risky`, two strikes
- [Two agents at once](operating/two-agents.md): two agents on one machine: worktrees, the assignee, the device lock
- [Host C++](operating/host-build.md): the one CMake build of the tests', host tools' and simulator's C++ (`hosttest/`)
- [Development tools](operating/dev-tools.md): mise, uv, pnpm, the private folder and toolchain (`xyf.local.json`), Open-XY upstream
