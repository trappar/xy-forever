# XY Forever

**Source preview.** XY Forever is in development and has been tested on one OP-XY.
The public firmware release and hosted installer are pending.

A firmware and mod manager for the OP-XY. It runs at power-on, before the OS: pick
which firmware boots from a library on the device, turn mods on and off, and keep
everything updated, all without touching the bootloader. With no key held, the default
firmware boots at close to stock speed; hold PLAY at power-on to open the menu.

XY Forever is a chain-loaded second-stage `os.ldr`, not a bootloader: the real 1.2.1
bootloader stays untouched and loads it from the NAND boot partition, and the dev menu
(hold COM) remains available for recovery.

## Try the source

Clone the repository and run a host-only check. These commands need git and
[mise](https://mise.jdx.dev/installing-mise.html), which installs every other pinned tool.

```sh
git clone https://github.com/trappar/xy-forever.git
cd xy-forever
mise trust && mise install
mise run test tests/test_setup.py tests/test_protocol_spec.py
```

Full development setup needs no OP-XY, about six commands: `xcode-select --install`,
`brew install mise libusb`, clone and `mise trust && mise install`, `mise run setup`, `gh auth login`
([ONBOARDING.md](ONBOARDING.md)). `mise tasks` lists the commands. Firmware builds and device work
need the stock files from your own OP-XY on stock 1.2.1
([first device install](docs/operating/first-install.md)); the built-in updater cannot downgrade a 1.2.5 device.
The GitHub issues and milestones track work toward the first firmware release.

## Contributing with a coding agent

Make an empty workspace folder, open your coding agent in it and paste this prompt:

> Set me up to develop XY Forever in this folder. Clone https://github.com/trappar/xy-forever.git,
> read xy-forever/AGENTS.md, and follow xy-forever/ONBOARDING.md. Run each command and ask
> me only for what needs my hands or my decision. When setup is done, tell me how to
> start the next session, and whether to do the first device install now.

Later sessions: open your agent in the workspace folder (or `xy-forever/`) and say "Continue
development". `AGENTS.md` is the entry point; if your harness does not load it automatically,
ask it to read that file first. Bootstrap also writes a workspace `CLAUDE.md` for Claude Code.
The [workflow](docs/ORCHESTRATION.md) also supports agents without delegation.
For OP-XY owners: [docs/GUIDE.md](docs/GUIDE.md) (the menu, the Mods page, the host
tool). For mod writers: [docs/MODS-DEV.md](docs/MODS-DEV.md).

## Layout and requirements

Where code lives: [docs/architecture/code-map.md](docs/architecture/code-map.md). Supported hosts and
everything setup installs: [ONBOARDING.md](ONBOARDING.md). Firmware builds need the Blackfin toolchain
(Open-XY's `toolchain-1` release, which `mise run setup` installs and checks against `toolchains.json`) and
private inputs outside the repository: the stock 1.2.1 `os.ldr` and `bootloader.ldr` from your own OP-XY,
which are not distributed. Tests that need them skip when they are absent.

## Build and verify after the first device install

`mise run build` and `mise run verify` build the product and run its audits; the tests and the PR gate:
[checks](docs/operating/checks.md). Installing on a device:
[docs/operating/build-install.md](docs/operating/build-install.md), under the rules in
[docs/SAFETY.md](docs/SAFETY.md).

## Native codec limits

- **ZIP:** ZIP32 only, stored or DEFLATE, with bounded portable ASCII names. ZIP64,
  encrypted entries and Unicode paths are refused.
- **LDR:** supports the observed RAM stream subset (no INIT, CALLBACK or compressed
  records), with an 8 MiB payload limit.
- **TFW:** decryption checks integrity and format; the vendor ECDSA P-224 header
  signature is verified natively (`p224`, `tfw_signature`). No keys are embedded.
- **Companions:** a companion update counts as done only when a runtime handshake or
  version check confirms it, never from simulated values. Companion programming is
  not wired into any build.

## Licence

MIT ([LICENSE](LICENSE)).
