# XY Forever

A boot menu and firmware manager for the OP-XY. Hold PLAY at power-on to pick a
firmware from a library on the device; with no key held, the default firmware
boots at close to stock speed. XY Forever is a chain-loaded second-stage `os.ldr`: the
real 1.2.1 bootloader stays untouched and loads it from the NAND boot partition,
and the dev menu (hold COM) remains available for recovery.

## Getting started

Make an empty workspace folder, open Claude Code in it and paste this prompt:

> Set me up to develop XY Forever in this folder. Clone the private GitHub repository
> trappar/xy-forever here with `gh repo clone trappar/xy-forever`, then read xy-forever/AGENTS.md
> and follow xy-forever/ONBOARDING.md with me from the first step. Run every command yourself; ask
> me only for what needs my hands (the OP-XY) or my decision. When setup is done, tell me how to
> start the next session.

Later sessions: open Claude Code in the workspace folder (or `xy-forever/`) and say "Continue
development". By hand instead (Python 3.9+; the workspace also holds `private/` and `tools/`):

```sh
mkdir xyf && cd xyf && gh repo clone trappar/xy-forever && cd xy-forever
python3 scripts/setup.py bootstrap      # private folder, toolchain download, local files, npm; safe to rerun
```

Step by step, with what bootstrap does and what it leaves to you (the stock files off your
OP-XY): [ONBOARDING.md](ONBOARDING.md).

Status: under development, installed and tested on one device. Not for general
use. Roadmap: the GitHub issues and milestones.

Supported devices: an OP-XY still on 1.2.1 only; a device updated to 1.2.5 cannot be
downgraded with the built-in firmware updater, so XY Forever cannot be installed on it.

For OP-XY owners: [docs/GUIDE.md](docs/GUIDE.md) (the menu, the Mods page, the host
tool). For mod writers: [docs/MODS-DEV.md](docs/MODS-DEV.md).

## Layout

```
xyf_build.py      build + verify a candidate os.ldr (CRC, call and symbol audits)
xyf_host.py       host tool for XY Forever's USB service (install, files, screen, keys)
ui_preview.py     render the device UI to PNG on the host
verify.py         cross-check the native LDR parser against official images
target/           cold-time device code (C++/asm, freestanding, both processors)
native/           freestanding codecs: LDR, SHA-256, inflate/ZIP, AES/TFW, packages, companions
xy_forever/       Python package: build helpers, report decoder, USB protocol, private paths
protocol/v1.json  the USB serial protocol spec; scripts/protogen.py generates its code and docs/PROTOCOL.md
clients/ts/       TypeScript protocol module (src/v1.ts, generated) for the web app; never built into firmware
web/              the web app (Chrome, Web Serial): mod package manager and file manager; see web/README.md
re/               reverse-engineering tools over the private 1.2.1 listing
scripts/          device session helpers; neutral.py compares candidates between a git ref and the working tree
tests/            unit tests (host-compiled C++ bridges + Python)
docs/             PLAN, STATUS, SAFETY, OPERATING, ARCHITECTURE, HARDWARE, STORAGE, GOALS, HISTORY,
                  ORCHESTRATION, GUIDE, MODS-DEV; decisions/ (settled questions)
```

## Requirements

- macOS (arm64) or Windows x86_64 host with Python 3.9+ (standard library only) and clang
  for the host tests. Node 22.18+ for `clients/ts` and `web/`.
- The Blackfin toolchain (`bfin-elf` GCC 8): Open-XY's `toolchain-1` release, which
  `setup.py bootstrap` downloads into `../tools/blackfin` (or `$XYF_TOOLCHAIN`). The archive is
  checked against `stock.json` and every file against `toolchains.json`.
- Private inputs outside the repo (`../private/` or `$XYF_PRIVATE`): the stock 1.2.1
  `os.ldr` and `bootloader.ldr`, and the decrypted official packages. They are not
  distributed. Tests that need them skip when they are absent. `python3 scripts/setup.py`
  creates the folder and imports the stock pair (`bootstrap`, `import-stock --from-device` or `DIR`, `doctor`).

## Quick start

```sh
python3 -m unittest discover -s tests
python3 xyf_build.py --xyf-product --out ../private/xy-forever/builds/my-build
python3 xyf_build.py --verify --out ../private/xy-forever/builds/my-build
python3 scripts/neutral.py        # after a refactor: every build mode's candidate is unchanged
```

Installing on a device: [docs/OPERATING.md](docs/OPERATING.md), under the rules in
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
