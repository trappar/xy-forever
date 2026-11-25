# xy-forever

Try your [Open-XY](https://github.com/etcetc000/open-xy) mod on an OP-XY running
[XY Forever](https://xy-forever.trappar.com), without cloning XY Forever. Open-XY builds the mod; `xyf` puts it on the
OP-XY, builds it there alone and boots it once. Your own saved mods stay as they are: the next plain start goes back
to them.

## Needs

- An OP-XY running XY Forever with protocol 1.30 or later (update at https://xy-forever.trappar.com, Manage → Device
  → Update), connected over USB.
- macOS or Linux, Python 3.11 or later, Node 22.12 or later (Open-XY's own requirements).
- An Open-XY checkout with its toolchain (`python sdk.py toolchain fetch`) and your machine (`python sdk.py new`).

## Install

```sh
pip install xy-forever          # in the environment you run sdk.py with; the command is xyf
uvx xy-forever dev KIT          # or without installing: uvx (or pipx run) runs xyf as xy-forever
```

## The loop

Run these from your Open-XY checkout. `sdk.py kit` needs a copy of the stock 1.2.1 `os.ldr` (Open-XY's
[build guide](https://github.com/etcetc000/open-xy/blob/staging/docs/build.md), step 2).

```sh
python sdk.py kit --personal --machine ../mymachine --stock-os STOCK/os.ldr --out ../builds/mine-001
xyf dev ../builds/mine-001      # push, build on the OP-XY, boot once
```

After an edit, build a new kit (a new `--out` folder) and run `xyf dev` on it again. Or leave
`xyf dev --watch ../builds` running: it does the same for every new kit in that folder. `xyf logs` copies the OP-XY's
new crash logs and XY Forever's reports of the last boot into `./xyf-logs` and prints the text ones.

`xyf dev` takes the kit's one personal machine (or extension); with several, name one with `--only ID`.
`xyf --help` and `xyf dev --help` list the rest.

## For AI agents

Run every step in a terminal and read its output; only the power cycle needs the person at the OP-XY.

The loop: edit the machine → `python sdk.py kit --personal --machine M --stock-os S --out NEW_FOLDER` → `xyf dev
NEW_FOLDER` → if it prints the power line, ask the person to do exactly that → read the result → `xyf logs` when the mod
misbehaved or crashed.

What each line means:

| Line | Meaning |
|---|---|
| `Power the OP-XY off, then on holding PLAY (let go at the logo)` | The OP-XY is off or running its OS. Ask the person to do this; `xyf` waits (up to `--wait`, 600 s). |
| `[wait] 12.30 s` | The OP-XY's XY Forever menu answered after that wait. |
| `[push] 1.20 s: ...` | The mod and its base are on the OP-XY; only changed files were sent. |
| `[build] 8.10 s: ...` | The OP-XY built the mod alone (the build's library entry). |
| `[boot] 3.00 s: ... started once` | Its content is installed and the OS is starting with the mod: try it now. |
| `total 12.30 s` | The run ended. Exit code 0 only when the mod booted. |

What to do on each failure (the line starts with `xyf dev:` or `xyf logs:`, or a phase line says `refused` or
`failed`):

| Output | Do |
|---|---|
| `refused: ... holds no kit.json` | Give the folder `sdk.py kit --out` wrote. |
| `refused: the kit holds N mods (...)` | Add `--only ID`. |
| `refused: no Open-XY checkout at ...` | Run from the Open-XY checkout, or add `--sdk DIR`. |
| `refused: Node is missing ...` | Install Node 22.12 or later, then run it again. |
| `failed: the kit could not be split: ...` | The kit or Open-XY's `core.mjs` failed: fix what it names, build a new kit. |
| `the OP-XY speaks XY Forever protocol X; xyf needs Y ... Update XY Forever at ...` | Ask the person to update XY Forever on the OP-XY as the line says (or, when it says so, update xyf). |
| `XY Forever USB service not found ...` | The menu never answered: check the USB cable, then ask for the power cycle again. |
| `[build] ...: refused: ...` or `failed: ...` | The OP-XY would not build the mod (the reason follows): a mod over the memory limit, a base it does not know (add `--unverified` only if the person trusts the kit), no stock firmware (`--base ID`). Fix it and build a new kit. |
| `[boot] ...: the OP-XY stayed in its menu: ...` | The build did not start; the reason follows. Run `xyf logs`. |
| `connection lost (...)` | Run the same command again. |
| `stopped` (exit 130) | Ctrl-C. |

If the mod crashes the OP-XY, ask for the power cycle holding PLAY, then run `xyf logs` and read what it printed and
saved.

## Compatibility

`xyf` checks the protocol the OP-XY reports when it connects. This version speaks XY Forever protocol 1.30 or a later
1.x; `xyf --version` says so. The package has its own version numbers, separate from XY Forever's: `pip install
xy-forever` gets the latest normal version, `pip install --pre xy-forever` also the newest development version.
