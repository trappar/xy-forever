# xy-forever

Boot your [Open-XY](https://github.com/etcetc000/open-xy) build on an OP-XY running
[XY Forever](https://xy-forever.trappar.com), without cloning XY Forever. Open-XY builds the firmware; `xyf` checks it,
puts it on the OP-XY with its content and boots it once. Your own setup stays as it is: the next plain start goes back
to it.

## Needs

- An OP-XY running XY Forever with protocol 1.30 or later (update at https://xy-forever.trappar.com, Manage → Device
  → Update), connected over USB.
- macOS, Linux or Windows, Python 3.11 or later.
- An Open-XY checkout with its toolchain (`python sdk.py toolchain fetch`) and your machine (`python sdk.py new`).

## Install

```sh
pip install xy-forever          # in the environment you run sdk.py with; the command is xyf
uvx xy-forever dev BUILD        # or without installing: uvx (or pipx run) runs xyf as xy-forever
```

## The loop

Run these from your Open-XY checkout. `STOCK` is where `xyf stock-os` puts the stock OS, and it prints the path:
`~/Library/Application Support/OPXY-SDK/private/stock-1.2.1/os.ldr` on macOS, the same under `~/.local/share/` on
Linux and `%LOCALAPPDATA%` on Windows.

```sh
xyf stock-os                                               # once: the stock OS 1.2.1 from your OP-XY's library
python sdk.py build --machine ../mymachine --stock-os STOCK --out ../builds/mine-001
xyf dev ../builds/mine-001                                 # the folder sdk.py build --out wrote
```

After an edit, build again into a new `--out` folder (or with `--overwrite`) and run `xyf dev` on it. Or leave
`xyf dev --watch ../builds` running: it does the same for every new build in that folder. `xyf logs` copies the OP-XY's
new crash logs and XY Forever's reports of the last boot into `./xyf-logs` and prints the text ones.

## For AI agents

Run every step in a terminal and read its output; only the power cycle needs the person at the OP-XY.

The loop: edit the machine → `python sdk.py build --machine M --stock-os STOCK --out NEW_FOLDER` → `xyf dev
NEW_FOLDER` → if it prints the power line, ask the person to do exactly that → read the result → `xyf logs` when the
mod misbehaved or crashed. Run `xyf stock-os` once first; it prints where `STOCK` is.

What each line means:

| Line | Meaning |
|---|---|
| `Power the OP-XY off, then on holding PLAY (let go at the logo)` | The OP-XY is off or running its OS. Ask the person to do this; `xyf` waits (up to `--wait`, 600 s). |
| `[wait] 12.30 s` | The OP-XY's XY Forever menu answered after that wait. |
| `[push] 41.20 s: xyf-dev: os.ldr 2318 KB, 2 of 2 content files sent` | The build is on the OP-XY as library entry `xyf-dev`. |
| `[boot] 3.00 s: xyf-dev started once; ...` | Its content is installed and it is starting: try it now. |
| `total 44.20 s` | The run ended. Exit code 0 only when the build booted. |

What to do on each failure (the line starts with `xyf dev:`, `xyf stock-os:` or `xyf logs:`, or a step line says
`refused` or `failed`):

| Output | Do |
|---|---|
| `refused: ... holds no candidate/os.ldr` | Give the folder `sdk.py build --out` wrote. |
| `refused: no stock OS 1.2.1 at ...` | Run `xyf stock-os`, or give `--stock-os FILE`. |
| `refused: no Open-XY profile at ...` | Run from the Open-XY checkout, or add `--sdk DIR`. |
| `refused: the candidate os.ldr is not an Open-XY build of OS 1.2.1 ...` (or it differs, or its module) | Build with `sdk.py build` from the stock OS `xyf stock-os` wrote; never edit the image. |
| `refused: the OP-XY has no content list of OS 1.2.1 yet ...` | Ask the person to save mods once on the OP-XY's Mods page, then run it again. |
| `the OP-XY speaks XY Forever protocol X; xyf needs Y ... Update XY Forever at ...` | Ask the person to update XY Forever as the line says (or, when it says so, update xyf). |
| `XY Forever USB service not found ...` | The menu never answered: check the USB cable, then ask for the power cycle again. |
| `[push] ...: failed: ...` | The OP-XY refused a step of the copy (the reason follows). Run it again; if it repeats, report it. |
| `[boot] ...: refused: ...` or `the OP-XY stayed in its menu: ...` | The build did not start; the reason follows (a content file Open-XY may not replace, say). Run `xyf logs`. |
| `connection lost (...)` | Run the same command again. |
| `stopped` (exit 130) | Ctrl-C. |

If the build crashes the OP-XY, ask for the power cycle holding PLAY, then run `xyf logs` and read what it printed
and saved.

## Compatibility

`xyf` checks the protocol the OP-XY reports when it connects. This version speaks XY Forever protocol 1.30 or a later
1.x; `xyf --version` says so. The package has its own version numbers, separate from XY Forever's: `pip install
xy-forever` gets the latest normal version, `pip install --pre xy-forever` also the newest development version.
