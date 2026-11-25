# Writing mods for XY Forever

For people who write Open-XY mods (machines and extensions) and want to try them on an OP-XY running XY Forever,
with no XY Forever checkout: Open-XY builds the firmware, and `xyf` (the `xy-forever` package on PyPI) checks it, puts
it on the OP-XY with its content and boots it once. Any other full firmware image works too. An agent can run every step but the power cycle. What each output
line means and what to do on each failure: the package's page, [pypi/README.md](../pypi/README.md).

## What you need

- An OP-XY running XY Forever, protocol 1.30 or later (update it in the web app: Manage → Device → Update).
- macOS, Linux or Windows, Python 3.11 or later, and what Open-XY's
  [build guide](https://github.com/etcetc000/open-xy/blob/staging/docs/build.md) asks for (its toolchain).

## Once

```sh
git clone https://github.com/etcetc000/open-xy && cd open-xy
python sdk.py toolchain fetch                     # on Linux: built with Docker (the build guide)
python sdk.py new --repository --id mymachine --name "My Machine" --out ../mymachine
pip install xy-forever                            # the same environment; or no install: uvx xy-forever
xyf stock-os                                      # the stock OS 1.2.1, from your OP-XY's own library
```

`xyf stock-os` copies the library entry whose SHA-256 is stock OS 1.2.1's, checks it and writes it where Open-XY's
docs keep it: `OPXY-SDK/private/stock-1.2.1/os.ldr` under `~/Library/Application Support/` (macOS), `~/.local/share/`
(Linux) or `%LOCALAPPDATA%` (Windows). It prints the path. The build needs it; Open-XY builds every firmware from it.

## Each edit

From the Open-XY checkout:

```sh
python sdk.py build --machine ../mymachine --stock-os STOCK/os.ldr --out ../builds/mine-001
xyf dev ../builds/mine-001
```

Each build needs a new `--out` folder (or `--overwrite`). `xyf dev` takes that folder (it holds `candidate/os.ldr` and
`candidate/content/`), any folder holding an `os.ldr` (and optionally `content/`), or an `os.ldr` itself. It first
checks what XY Forever checks before it boots a library image (an OS image, its size, entry point and where it
writes) and refuses early in plain words. It says which it is: `Open-XY build on 1.2.1` when the image is exactly
Open-XY's build on the stock OS (compared with `xyf stock-os`'s copy and the checkout's profile), else `custom image`.
Then one line per step, each with its time:

- `Power the OP-XY off, then on holding PLAY (let go at the logo)`: only when the OP-XY is off or in its OS; then
  `[wait]` once its XY Forever menu answers.
- `[push]`: the image as library entry `xyf-dev`, with its content files over the official set (none: the official
  content); the official content is on the OP-XY already.
- `[boot]`: its content installed, the OS starts with it, once.
- `total`. Exit code 0 only when the build booted; a refusal says why in plain words (exit 2).

Nothing saved changes: the next plain power-on goes back to your own setup. If an image does not start or crashes,
power on holding PLAY: the XY Forever menu opens whatever was booted, ready for the next `xyf dev`. `xyf dev --watch ../builds` repeats this
for each new build in that folder (or the folder itself, rebuilt), until Ctrl-C. From outside the checkout, add
`--sdk OPEN_XY` (Open-XY's profile of OS 1.2.1 is read from it).

When the mod misbehaves or crashes the OS: power on holding PLAY, then `xyf logs`. It copies the OS's crash logs
(`/yaffs2/logs`) and XY Forever's reports of the last boot that are new since its last run into `./xyf-logs`, and
prints the text ones.

`xyf` checks the protocol the OP-XY reports when it connects. An XY Forever too old for it stops it with one line
naming both protocols and where to update; one newer than it speaks asks for `pip install -U xy-forever`.

## Limits

- **Memory:** the mods share one region of fixed size (Open-XY's profile). `sdk.py build` refuses a build over it.
- **Content:** a build may not replace official content files (the device refuses the switch); the catalog,
  `op1_factory.json`, is the one it may replace.
- **The push** sends the whole `os.ldr` (about 2.3 MB) each time a build changes it.
- **The first `xyf dev`** on an OP-XY that never saved mods records its official OS 1.2.1 content once first
  (`[content]`, about 45 s on the device, "Reading OS 1.2.1 content" on its screen); later runs skip it.

## Not yet

- No CPU or stability checks before a build boots. One that crashes the OS needs a power-on holding PLAY to get back
  to the menu (not verified for every crash). B's own crash records do not survive a power cycle yet (#295).
- Only the stock 1.2.1 base. Choosing another base firmware is planned.

## For XY Forever contributors

The mod package tools (`modkit split`, `xyf-host mods push`, the package format): [mods-dev/packages.md](mods-dev/packages.md).
`xyf-host mods dev`, a mod built on the device from packages: [mods-dev/try.md](mods-dev/try.md). The package and its
publishing: [operating/pypi.md](operating/pypi.md).
