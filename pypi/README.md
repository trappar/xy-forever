# xy-forever

Boot your [Open-XY](https://github.com/etcetc000/open-xy) build, or any other full firmware image, on an OP-XY running
[XY Forever](https://xy-forever.trappar.com), without cloning XY Forever. `xyf` checks the image as XY Forever checks
the firmware in its library, puts it on the OP-XY with its content (if it has any) and boots it once. Your own setup
stays as it is: the next plain start goes back to it, and holding PLAY at power-on always opens the XY Forever menu.

## Needs

- An OP-XY running XY Forever with protocol 1.31 or later (update at https://xy-forever.trappar.com, Manage → Device
  → Update), connected over USB.
- macOS, Linux or Windows, and Python 3.11 or later in a virtual environment (a stock Mac has only `python3` 3.9 and
  no `python`; [uv](https://docs.astral.sh/uv/) brings its own Python).
- An Open-XY checkout with its toolchain (`python sdk.py toolchain fetch`) and your machine (`python sdk.py new`).
  Open-XY's `sdk.py` runs in the same environment.

## Install

In your Open-XY checkout:

```sh
uv venv --python 3.12 .venv && source .venv/bin/activate    # or: python3.11 -m venv .venv (Windows: .venv\Scripts\activate)
uv pip install xy-forever                                    # with python3.11 -m venv: pip install xy-forever; the command is xyf
```

Or without installing: `uvx xy-forever dev BUILD` (or `pipx run xy-forever …`) runs `xyf` as `xy-forever`.

## The loop

In the checkout, with that environment active:

```sh
xyf stock-os            # once: prints "xyf stock-os: STOCK (2373336 bytes, SHA-256 checked, ...)"
python sdk.py build --machine ../mymachine --stock-os "STOCK" --out ../builds/mine-001
xyf dev ../builds/mine-001
```

`STOCK` is the path `xyf stock-os` printed: the os.ldr itself, under Open-XY's private folder
(`~/Library/Application Support/OPXY-SDK/private/stock-1.2.1/os.ldr` on macOS, the same under `~/.local/share/` on
Linux and `%LOCALAPPDATA%` on Windows); quote it. After an edit, build into a new folder with the last build's machine
numbers, as Open-XY's build asks: `python sdk.py build --machine ../mymachine --stock-os "STOCK" --layout
../builds/mine-001/layout.json --out ../builds/mine-002`, then `xyf dev ../builds/mine-002`. Or leave `xyf dev --watch
../builds` running: it does the same for every new build in that folder.

Any other firmware works the same way: `xyf dev FOLDER` (holding `os.ldr`, and optionally `content/` with files to put
over the official content) or `xyf dev path/to/os.ldr`. The push line says `Open-XY build on 1.2.1` when the image is
exactly Open-XY's build on the stock OS (checked against the stock os.ldr and your checkout's profile), else `custom
image`; without content it boots with the official content. `xyf logs` copies the OP-XY's new crash logs and XY
Forever's reports of the last boot into `./xyf-logs` and prints the text ones.

## For AI agents

Run every step in a terminal and read its output; only the power cycle needs the person at the OP-XY.

The loop, in the Open-XY checkout with its Python 3.11+ environment active: edit the machine → `python sdk.py build
--machine M --stock-os "STOCK" --layout LAST/layout.json --out NEW_FOLDER` (no `--layout` the first time) → `xyf dev
NEW_FOLDER` → if it prints the power line, ask the person to do exactly that → read the result → `xyf logs` when the
mod misbehaved or crashed. Run `xyf stock-os` once first; `STOCK` is the path it prints. Each command waits up to
`--wait` (600 s) for the menu: leave it at that, the power cycle takes the person a while.

What each line means:

| Line | Meaning |
|---|---|
| `Power the OP-XY off, then on holding PLAY (let go at the logo)` | The OP-XY is off or running its OS. Ask the person to do this; `xyf` waits (up to `--wait`, 600 s). |
| `[wait] 12.30 s` | The OP-XY's XY Forever menu answered after that wait. |
| `[push] 41.20 s: xyf-dev: Open-XY build on 1.2.1, os.ldr 2318 KB, 2 of 2 content files sent` | The build is on the OP-XY as library entry `xyf-dev` (or `custom image`; `the official content` when it has none). |
| `[content] 43.10 s: recorded the official 1.2.1 content (once)` | Only the first time on an OP-XY that never saved mods: it read its official content (the OP-XY shows "Reading OS 1.2.1 content"). |
| `[boot] 3.00 s: xyf-dev started once; ...` | Its content is installed and it is starting: try it now. |
| `total 44.20 s` | The run ended. Exit code 0 only when the build booted. |

What to do on each failure (the line starts with `xyf dev:`, `xyf stock-os:` or `xyf logs:`, or a step line says
`refused` or `failed`):

| Output | Do |
|---|---|
| `refused: ... holds no candidate/os.ldr or os.ldr` | Give the folder `sdk.py build --out` wrote, or an os.ldr. |
| `refused: the os.ldr is ...` (not an OS image, its size, where it starts or writes) | XY Forever would not boot it either: fix the build. |
| `custom image` where you expected `Open-XY build on 1.2.1` | Not an error. Run `xyf stock-os` and run from the Open-XY checkout (or add `--sdk DIR`) so it can be compared. |
| `the OP-XY speaks XY Forever protocol X; xyf needs Y ... Update XY Forever at ...` | Ask the person to update XY Forever as the line says (or, when it says so, update xyf). |
| `the XY Forever menu did not answer within N s (--wait)` (or `XY Forever USB service not found ...`) | The menu never answered in time: check the USB cable, ask for the power cycle holding PLAY again and run it again. |
| `[push] ...: failed: ...` | The OP-XY refused a step of the copy (the reason follows). Run it again; if it repeats, report it. |
| `[boot] ...: refused: ...` or `the OP-XY stayed in its menu: ...` | The build did not start; the reason follows (a content file that would replace an official one, say). Run `xyf logs`. |
| `connection lost (...)` | Run the same command again. |
| `stopped` (exit 130) | Ctrl-C. |

If the build crashes the OP-XY or does not start, ask for the power cycle holding PLAY (the XY Forever menu opens,
whatever was booted), then run `xyf logs` and read what it printed and saved. A plain power-on boots the saved setup
again.

## Compatibility

`xyf` checks the protocol the OP-XY reports when it connects: `xyf dev` needs XY Forever protocol 1.31 or a later 1.x,
`xyf logs` and `xyf stock-os` 1.5 or later; `xyf --version` says so. The package has its own version numbers, separate from XY Forever's: `pip install
xy-forever` gets the latest normal version, `pip install --pre xy-forever` also the newest development version.
