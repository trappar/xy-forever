# Onboarding

From an empty workspace folder to working on the plan, for the developer's coding agent: follow
the steps in order with the developer. Run every command yourself; ask the developer only for what needs their
hands (the OP-XY, a file copy over MTP) or their decision, one question at a time, each with your
recommendation. Stop at the first step that fails, fix what is yours to fix, say plainly what is
missing, and resume there. Steps 7 and 8 (the device) have not yet been run end to end: report
where they stall, and fix this file. Budget: 10 KB (10000 bytes).

## 0. What the developer needs

- A host computer: macOS on Apple silicon (the tested host; toolchain pins also exist for Windows
  and Linux x86_64, USB tools untested there). Python 3.9+ (macOS's own `python3` will do), git,
  `gh` (GitHub CLI, logged in: `gh auth status`; step 1 clones with it, step 9 claims items),
  clang++ (simulator, host bridges), Node 22.18+ or 23.6+ (`clients/ts`, `web/`).
- An OP-XY still on stock 1.2.1, never updated to 1.2.5: a 1.2.5 device cannot run XY Forever,
  and teenage engineering's built-in updater cannot downgrade it. To tell: the version the OS shows
  is 1.2.1, te-boot (hold COM while switching on) shows "3. dev menu" (1.2.5 has none; on 1.2.1,
  Open-XY's browser builder can enable it: open-xy `docs/builder.md`), and step 4 confirms it.
- A USB cable and an MTP client: OpenMTP on macOS (quit Field Kit first), File Explorer on
  Windows, the file manager on Linux.

## 1. Bootstrap the workspace

Start in the workspace folder (where Claude Code was opened); it ends up as:
```text
<workspace>/
  AGENTS.md, CLAUDE.md  the workspace guide (bootstrap writes them if absent; never overwritten)
  xy-forever/      this checkout
  tools/blackfin/  open-xy's toolchain-1 release, unpacked by bootstrap (or anywhere with XYF_TOOLCHAIN)
  private/         firmware, builds, device reports, evidence, dev-menu kits: never in git
```
- Run-time loading (#28) lives on the private fork `trappar/open-xy`, branch `runtime-loading`
  (on upstream `ba943a8`): clone it to `<workspace>/open-xy` only for items that need it. It is not
  the pinned upstream clone `fixtures` makes for open-xy facts (AGENTS.md).

```sh
gh repo clone trappar/xy-forever      # only if xy-forever/ is absent (from the workspace folder)
cd xy-forever
python3 scripts/setup.py bootstrap    # only what is missing; never deletes; --dry-run: print, change nothing
```

Rerun it even if the developer already did (README). Each step acts only when its result is missing:

- **Private folder:** `private/`'s layout (`xy_forever/paths.py`) and `private/incoming/` (step 4).
  Elsewhere: `XYF_PRIVATE`, or `setup.py init --private DIR` first (recorded in `xyf.local.json`).
- **Workspace guide** (`AGENTS.md`, `CLAUDE.md`), **toolchain** (step 2), **local files** (step 3),
  **`npm ci`** in `clients/ts`, `web/` (skipped without Node), with the stock pair `fixtures` (step 5).
- It asks for nothing and ends with `doctor` and a "Next:" list (steps 4, 5). It stops with a message
  (a rerun carries on) when the download fails or `tools/blackfin` fails its pins (move it aside).

## 2. Toolchain

Open-XY publishes the pinned toolchain (`toolchain-1`) for macOS arm64 and Windows x86_64. Unless
`doctor`'s toolchain row is ok, bootstrap downloads this host's release zip, checks its SHA-256
(`stock.json`), unpacks it into `tools/blackfin` and checks every file against `toolchains.json`.
Linux x86_64: it points at open-xy `docs/build.md`, "On Linux" (Docker); put the result there yourself.

- No download (proxy, certificates): get the zip from the `toolchain-1` release page in a browser and
  run `python3 scripts/setup.py bootstrap --toolchain-archive <zip>`. Never edit the pins.
- Reverse-engineering tools (Ghidra, a JDK, BlackFinPlus, a `.tfw` decryptor): research items only, under `tools/`
  ([docs/hardware/tools.md](docs/hardware/tools.md)).

## 3. Local files and leak patterns

Bootstrap copies `docs/STATUS.template.md` to `docs/status/README.md`, the index of a gitignored folder
for device and session state, and gives `xyf.local.json` an empty `"leak_patterns"` list. Ask the developer for their name,
email addresses and host name; add them there as regexes, which the leak test refuses in tracked files
without them entering the repository. Case-sensitive: `(?i)` first matches any case (`"(?i)jane ?doe"`).

## 4. Stock files from the developer's OP-XY

XY Forever builds on the stock 1.2.1 `os.ldr` and `bootloader.ldr`. They are not distributed;
the developer copies them off their own device. Give these steps one at a time:

1. Switch the OP-XY off. Hold **COM** while switching it on: te-boot shows "3. dev menu".
2. Press **Track 3** ("3. dev menu"), then **Track 2** ("2. mtp file transfer (mac)"; Track 3 is
   the Windows and Linux mode). Wait for "mtp mode active".
3. In the MTP client, copy `/upgrade/firmware/blackfin/bin/os.ldr` and `bootloader.ldr` (or the
   whole `/upgrade/` folder) to a folder outside every checkout, e.g. `private/incoming/`.
4. Press **Track 2** ("2. exit"), then switch the OP-XY off.

```sh
python3 scripts/setup.py import-stock ../private/incoming    # checks both SHA-256 pins (stock.json)
```

- **1.2.5 files: stop.** This device is on 1.2.5 and cannot run XY Forever: the 1.2.5 bootloader
  has no dev menu, and the built-in updater cannot downgrade it (SAFETY.md, "Never").
- Official `.tfw` packages and update keys are never distributed or made from the stock pair: their tests skip (`doctor`).

## 5. Fixtures, doctor, tests

```sh
python3 scripts/setup.py fixtures     # network: public open-xy and its kit; builds and verifies one product build
python3 scripts/setup.py bootstrap    # npm ci in clients/ts and web/ if Node came later; else "nothing to do"
python3 scripts/setup.py doctor       # every row "ok"; exit 0
python3 scripts/run_tests.py          # the full suite; the first run compiles host bridges (slower)
```

Done when `doctor` exits 0 and the suite passes (the skips `doctor` lists are expected).

## 6. The developer's own approvals

Read [docs/SAFETY.md](docs/SAFETY.md) first: it binds all device work. Device work needs the developer's
explicit yes (SAFETY.md, ORCHESTRATION.md "Approvals"); two kinds can be given once, as standing approvals for
their own device. Ask each separately, with your recommendation; record the answer and its scope in the local
`docs/status/README.md` ("Standing approvals"), never in git: approvals belong to one developer and one device.

1. **Host access:** agents may operate this OP-XY through XY Forever's own USB service
   (`xyf_host.py` and the scripts built on it), under SAFETY.md. Recommend yes: every device item
   needs it. Without it, device items wait for a yes each time.
2. **Routine USB installs:** a build that passed `--verify` and the full suite, carrying one understood change,
   is installed without a separate yes; what is being installed is still said first. New or risky behaviour
   (storage writes, new every-boot paths, new reset behaviour) is asked each time. Recommend yes.

Never standing: anything SAFETY.md lists under "Never", companion chips, 1.2.5, relaxing a
builder audit, a new kind of storage write. Each is asked for that exact operation.

## 7. First install and recovery kit (not yet run end to end)

First the dev-menu kit that undoes the install (SAFETY.md, recovery ladder step 2):

```sh
mkdir -p ../private/xy-forever/recovery/stock-1.2.1
cp ../private/firmware/stock-1.2.1/os.ldr ../private/xy-forever/recovery/stock-1.2.1/os.ldr
```

The first install goes through the dev menu (no XY Forever service yet). Install the `candidate.ldr`
of the build `fixtures` verified (`doctor`, "verified build"), as in [docs/OPERATING.md](docs/OPERATING.md#dev-menu):
COM at power-on, Track 3, Track 2 (MTP), copy it to the device as `/os.ldr` (not 0 bytes), Track 2
(exit), Track 5 ("write os to boot partition"), wait for "firmware written ok!", power cycle.

Then: start `python3 xyf_host.py --timeout 120 status`, and ask the developer to power on holding PLAY and let
go at the logo. The XY Forever menu opens ([docs/GUIDE.md](docs/GUIDE.md)); with an empty library it says "No
firmware found". Add the stock OS as the default entry (`library-add`, then `set-default`; check each command's
`--help`, run it without `--yes-…` first). Undo: `xyf_host.py uninstall`, or the stock kit through the dev menu.

## 8. Device pass and known-good kit (not yet run end to end)

Run `python3 scripts/device_pass.py --wait 120` (`--plan` first shows every step), relaying its `NEED:`
line ([docs/OPERATING.md](docs/OPERATING.md#device-pass-scriptsdevice_passpy)). When it passes, copy the
build's `candidate.ldr` to `../private/xy-forever/recovery/known-good-NNN/os.ldr` and fill in `docs/status/README.md`: device
state, installed build, known-good build and kit. End the session in keepalive, never in the OS.
No device at hand: say so; device items wait and the orchestrator skips them.

## 9. Continue development

Later sessions: open Claude Code in the workspace folder (or `xy-forever/`) and say "Continue
development". It runs the orchestrator ([docs/ORCHESTRATION.md](docs/ORCHESTRATION.md)): claim a
plan item's GitHub issue, then one branch and one pull request per item. While the repository has
no GitHub remote (`git remote` prints nothing), work stays on the local `main`.
