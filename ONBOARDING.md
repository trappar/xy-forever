# Onboarding

From an empty workspace folder to working on the plan, for the developer's coding agent. Run every command
yourself; ask the developer only for what needs their hands or their decision, one question at a time, with
your recommendation. Stop at the first step that fails, fix what is yours to fix, say plainly what is missing,
and resume there.

Two parts: **setup without a device** (below; enough for host code, the web app, docs and tests) and the
**[first device install](docs/operating/first-install.md)**, only when there is an OP-XY on stock 1.2.1 to work with.

## Setup without a device

A macOS host on Apple silicon with Homebrew: the tested one (CI runs the suite there). The Blackfin toolchain is
pinned for Windows x86_64 (downloaded) and Linux x86_64 (built with Docker) too; the rest is untested there
(install mise as https://mise.jdx.dev/installing-mise.html shows). Start in the workspace folder, where the
coding agent was opened:

```sh
xcode-select --install                 # git and clang++ (host tests)
brew install mise libusb               # mise (then `mise activate` in your shell profile); libusb for USB tools
git clone https://github.com/trappar/xy-forever.git && cd xy-forever && mise trust && mise install
mise run setup                         # private folder, toolchain, Python and JS dependencies (rerun: safe)
gh auth login                          # once: issues and pull requests
```

Skip what is already there (`gh auth status`, `xcode-select -p`, `mise --version`). `mise install` gets the
pinned Python, uv, Node, pnpm and gh (`mise.toml`), and also the tools of your global mise config
(`MISE_IGNORED_CONFIG_PATHS=~/.config/mise mise install` skips those); in a shell without `mise activate`, prefix
commands with `mise exec --`. `mise run setup` (`--dry-run`: print, change nothing)
acts only on what is missing and never deletes:

- **Private folder:** `../private/` (layout: `xy_forever/paths.py`): builds, sessions and your status notes
  (`xy-forever/status/README.md`, from `docs/STATUS.template.md`). Elsewhere: `uv run scripts/setup.py init
  --private DIR` first (recorded in `xyf.local.json`), or `XYF_PRIVATE`.
- **Workspace guide** (`AGENTS.md`, `CLAUDE.md` beside the clone; never overwritten).
- **Toolchain:** Open-XY's `toolchain-1` release for this host, checked against `stock.json` and every file
  against `toolchains.json`, into `../tools/blackfin`. No download (proxy, certificates): get the zip from the
  release page in a browser, then `mise run setup --toolchain-archive <zip>`. Linux: build it with Docker
  (open-xy `docs/build.md`, "On Linux"). Elsewhere: `uv run scripts/setup.py init --toolchain DIR` (recorded in
  `xyf.local.json`). Never edit the pins.
- **Dependencies:** `uv sync`, `pnpm install`, the git hook. Setup starts on mise's Python and continues under
  `uv run` once `uv sync` is done.
- It ends with `doctor` and a "Next:" list.

Then:

```sh
mise run doctor                        # exit 0: setup is done; the stock pair rows wait for the first device install
mise run test                          # the fast tier; tests that need the stock pair or fixtures skip, with the reason
```

**Leak patterns:** ask the developer for their name, email addresses and host name, and add them as regexes to
`"leak_patterns"` in `xyf.local.json` (setup creates the empty list). The leak test refuses them in tracked
files without them entering the repository. Case-sensitive: `(?i)` first matches any case (`"(?i)jane ?doe"`).

Reverse-engineering tools (Ghidra, a JDK, BlackFinPlus, a `.tfw` decryptor) are for research items only, under
`../tools/` ([docs/hardware/tools.md](docs/hardware/tools.md)).

## Continue development

Open your coding agent in the workspace folder (or `xy-forever/`) and say "Continue development". Ask it to
read `AGENTS.md` first if the harness does not load it; `CLAUDE.md` imports it for Claude Code. It follows
[ORCHESTRATION.md](docs/ORCHESTRATION.md): claim an issue, then one branch and PR per item. Device items wait
until the [first device install](docs/operating/first-install.md) is done.
