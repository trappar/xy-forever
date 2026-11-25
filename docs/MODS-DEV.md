# Writing mods for XY Forever

For people who write Open-XY mods (machines and extensions) and want to try them on an OP-XY running XY Forever,
with no XY Forever checkout: Open-XY builds the firmware, and `xyf` (the `xy-forever` package on PyPI) checks it, puts
it on the OP-XY with its content and boots it once. Any other full firmware image works too. An agent can run every step but the power cycle. What each output
line means and what to do on each failure: the package's page, [pypi/README.md](../pypi/README.md).

## What you need

- An OP-XY running XY Forever, protocol 1.31 or later (update it in the web app: Manage → Device → Update).
- macOS, Linux or Windows, and what Open-XY's
  [build guide](https://github.com/etcetc000/open-xy/blob/staging/docs/build.md) asks for (its toolchain).
- Python 3.11 or later in a virtual environment, for Open-XY's `sdk.py` and `xyf` alike. A stock Mac has only
  `python3` 3.9 and no `python`: [uv](https://docs.astral.sh/uv/) makes the environment with a Python of its own.

## Once

```sh
git clone https://github.com/etcetc000/open-xy && cd open-xy
uv venv --python 3.12 .venv                       # or: python3.11 -m venv .venv (any Python 3.11 or later)
source .venv/bin/activate                         # Windows: .venv\Scripts\activate; then python is 3.12
uv pip install xy-forever                         # with python3.11 -m venv: pip install xy-forever
python sdk.py toolchain fetch                     # on Linux: built with Docker (the build guide)
python sdk.py new --repository --id mymachine --name "My Machine" --out ../mymachine
xyf stock-os                                      # the stock OS 1.2.1, from your OP-XY's own library
```

`xyf stock-os` copies the library entry whose SHA-256 is stock OS 1.2.1's, checks it and writes it where Open-XY's
docs keep it, then prints that path: `xyf stock-os: STOCK (2373336 bytes, SHA-256 checked, from library entry
opxy-121) in 24.00 s`. `STOCK` below is that path: on macOS `~/Library/Application Support/OPXY-SDK/private/stock-1.2.1/os.ldr`,
on Linux the same under `~/.local/share/`, on Windows under `%LOCALAPPDATA%`. Quote it (it may hold spaces).

## Each edit

From the Open-XY checkout, in that environment:

```sh
python sdk.py build --machine ../mymachine --stock-os "STOCK" --out ../builds/mine-001
xyf dev ../builds/mine-001
python sdk.py build --machine ../mymachine --stock-os "STOCK" --layout ../builds/mine-001/layout.json \
    --out ../builds/mine-002                      # after an edit: the last build's machine numbers
xyf dev ../builds/mine-002
```

Each build needs a new `--out` folder (or `--overwrite`). `xyf dev` also takes any folder holding an `os.ldr` (and
optionally `content/`), or an `os.ldr` itself. It checks what XY Forever checks before it boots a library image and
says which it is (`Open-XY build on 1.2.1` or `custom image`). Then one timed line per step: the power line and
`[wait]` only when the OP-XY is off or in its OS; `[content]` once on an OP-XY that never saved mods; `[push]`;
`[boot]`; `total`. Exit code 0 only when it booted; a refusal says why (exit 2). What each line means:
[pypi/README.md](../pypi/README.md).

Nothing saved changes: the next plain power-on goes back to your own setup. If an image does not start or crashes,
power on holding PLAY: the XY Forever menu opens, ready for the next `xyf dev`; then `xyf logs` copies the OS's crash
logs and XY Forever's reports that are new into `./xyf-logs`. `xyf dev --watch ../builds` repeats for each new build
in that folder, until Ctrl-C. From outside the checkout, add `--sdk OPEN_XY`.

## Limits

- **Memory:** the mods share one region of fixed size (Open-XY's profile). `sdk.py build` refuses a build over it.
- **Content:** a build may not replace official content files (the device refuses the switch); the catalog,
  `op1_factory.json`, is the one it may replace.
- **The push** sends the whole `os.ldr` (about 2.3 MB) each time a build changes it.
- **The first `xyf dev`** on an OP-XY that never saved mods records its official content once (`[content]`, about
  45 s); later runs skip it.

## Not yet

- No CPU or stability checks before a build boots. One that crashes the OS needs a power-on holding PLAY to get back
  to the menu (not verified for every crash). B's own crash records do not survive a power cycle yet (#295).
- Only the stock 1.2.1 base. Choosing another base firmware is planned.

## For XY Forever contributors

The mod package tools (`modkit split`, `xyf-host mods push`, the package format): [mods-dev/packages.md](mods-dev/packages.md).
`xyf-host mods dev`, a mod built on the device from packages: [mods-dev/try.md](mods-dev/try.md). The package and its
publishing: [operating/pypi.md](operating/pypi.md).
