# The developer package on PyPI

Part of [Operating the device](../OPERATING.md). The package: `xy-forever` on PyPI, the `xyf` command (#284;
[MODS-DEV.md](../MODS-DEV.md)). Its page is `pypi/README.md`.

## What goes in

`pypi/pyproject.toml` lists the modules (`[tool.xyf] modules`); with `pypi/` and `LICENSE` they are the package's
inputs. `scripts/xyf_dist.py build` copies them into a fresh folder and runs `uv
build` there: a pure Python wheel and an sdist, source only, no Teenage Engineering bytes. The listed modules run on
Python 3.11, Open-XY's minimum: the root `pyproject.toml` lints them as 3.11 (`per-file-target-version`), and
`tests/test_xyf_dist.py` keeps that list equal to the package's and every import they make at load time inside it.
A module the package needs joins the list.

## The check

`.github/workflows/pypi.yml` runs on every pull request and push to `main` that touches `pypi/`, `xy_forever/`,
`LICENSE`, the script or the workflow, and on every XY Forever release. It builds the package on Linux, then runs
`xyf_dist.py check` on Linux and on Windows (uv only there, no repository environment): the wheel holds exactly the
listed files and stays small, a new Python 3.11 environment installs it, `xyf --help`, `xyf --version` and every module import run there, and `xyf logs` against a stand-in
OP-XY whose HELLO names protocol 1.21 stops with the mismatch line (exit 2). The simulator needs the private stock
bootloader, which CI never has.

## Versions and publishing

The package has its own version numbers, separate from XY Forever's. It publishes only when its inputs changed
(`xyf_dist.py plan`, `git diff` of the inputs):

- **A push to `main`** whose inputs differ from the push's previous commit publishes a pre-release `NEXT.devN`,
  N the commit count of `main` (unique and increasing). Only `pip install --pre xy-forever` takes it.
- **An XY Forever release** (a published, non-prerelease GitHub release, tag `v*`) publishes the normal version
  `NEXT` when the inputs changed since the last normal one, then tags that commit `xyf-vNEXT`. No change: nothing.
- `NEXT`: `pypi/pyproject.toml`'s version, or the patch after the newest `xyf-v` tag when that one is not lower.
  Raise the pyproject's version by hand for a minor or major step.

A pull request never publishes. The publish job runs only for those two events, in the `pypi` environment, through
PyPI trusted publishing (OIDC; the PyPI publisher names this repository, `pypi.yml` and the environment), so no
token is stored. A failed publish is retried by re-running its workflow run.
