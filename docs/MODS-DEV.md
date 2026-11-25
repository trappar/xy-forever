# Writing mods for XY Forever

For people who write Open-XY mods (machines and extensions) and want to try them on an OP-XY running XY Forever,
with no XY Forever checkout: Open-XY builds the mod, and `xyf` (the `xy-forever` package on PyPI) puts it on the
OP-XY, builds it there alone and boots it once. An agent can run every step but the power cycle. What each output
line means and what to do on each failure: the package's page, [pypi/README.md](../pypi/README.md).

## What you need

- An OP-XY running XY Forever, protocol 1.30 or later (update it in the web app: Manage → Device → Update).
- macOS or Linux, Python 3.11 or later, Node 22.12 or later, and what Open-XY's
  [build guide](https://github.com/etcetc000/open-xy/blob/staging/docs/build.md) asks for: its toolchain, and Clang
  with wasm32 for the kit's previews.
- A copy of the stock 1.2.1 `os.ldr` (the build guide, step 2).

## Once

```sh
git clone https://github.com/etcetc000/open-xy && cd open-xy
python sdk.py toolchain fetch                     # on Linux: built with Docker (the build guide)
python sdk.py new --repository --id mymachine --name "My Machine" --out ../mymachine
pip install xy-forever                            # the same environment; or no install: uvx xy-forever
```

## Each edit

From the Open-XY checkout:

```sh
python sdk.py kit --personal --machine ../mymachine --stock-os STOCK/os.ldr --out ../builds/mine-001
xyf dev ../builds/mine-001
```

Each kit needs a new `--out` folder (or `--overwrite`). `xyf dev` splits the kit with Open-XY's own
`builder/core.mjs` under Node, then prints one line per step, each with its time:

- `Power the OP-XY off, then on holding PLAY (let go at the logo)`: only when the OP-XY is off or in its OS; then
  `[wait]` once its XY Forever menu answers.
- `[push]`: the mod and its base, as a development set of their own; only changed files are sent.
- `[build]`: the OP-XY builds the mod alone, on your saved base and Mod memory.
- `[boot]`: its content installed, the OS starts with it, once.
- `total`. Exit code 0 only when the mod booted; a refusal says why in plain words (exit 2).

Nothing saved changes: the next plain power-on goes back to your own mods. `xyf dev --watch ../builds` repeats this
for each new kit in that folder (or the folder itself, rebuilt), until Ctrl-C. A kit with several machines needs
`--only ID`; from outside the checkout, add `--sdk OPEN_XY`.

When the mod misbehaves or crashes the OS: power on holding PLAY, then `xyf logs`. It copies the OS's crash logs
(`/yaffs2/logs`) and XY Forever's reports of the last boot that are new since its last run into `./xyf-logs`, and
prints the text ones.

`xyf` checks the protocol the OP-XY reports when it connects. An XY Forever too old for it stops it with one line
naming both protocols and where to update; one newer than it speaks asks for `pip install -U xy-forever`.

## Limits

- **Memory:** the mods share one region of fixed size, given by the base package
  (`module_bytes`). The Mods page shows an estimate from package sizes. Over the
  limit, it refuses to build ("Too big for the mods memory").
- **At most 64 machines and 8 extensions in one build.** At least one machine.
- **At most 96 packages** in a set are read by the Mods page.
- **The base must be a stock image XY Forever knows.** An unknown base is refused
  unless the user allows it once (SHIFT + PLAY on the Mods page's first row), or
  with `xyf dev --unverified`.
- A mod may not replace official content files.

## Not yet

- Windows: `xyf` needs macOS or Linux (its USB serial connection).
- No measuring build: the memory figure is an estimate, not the size after linking.
- No CPU or stability checks before a mod boots. A mod that crashes the OS needs a power-on holding PLAY to get back
  to the menu (not verified for every crash). B's own crash records do not survive a power cycle yet (#295).
- Only the stock 1.2.1 base. Choosing another base firmware is planned.

## For XY Forever contributors

The tools behind `xyf` (`modkit split`, `xyf-host mods push`, the package format): [mods-dev/packages.md](mods-dev/packages.md).
`xyf-host mods dev`, the device flow `xyf dev` runs: [mods-dev/try.md](mods-dev/try.md). Publishing the package:
[operating/pypi.md](operating/pypi.md).
