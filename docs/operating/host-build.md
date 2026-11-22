# Host C++

All host C++ is one CMake build, `hosttest/` (scikit-build-core, ninja); `uv sync` builds it in `build/hosttest` and
rebuilds only what an edit touched (`uv run` syncs first; `hosttest/pyproject.toml` lists the files that count).

- **The extension:** one nanobind module, `xyf_hosttest.module(NAME)`, a submodule per `hosttest/bind/NAME.cpp`.
- **The doctest binary:** the C++ unit tests in `tests/native/`, sanitized, run as one gate-tier test by
  `tests/test_doctest.py`.
- **Programs** (`xyf_program` in `hosttest/CMakeLists.txt`, found by `xyf_hosttest.program(NAME)`): what one shared
  build cannot serve. A library each test loads from its own copy (fresh static state), a define's variants
  (`XYF_MTP_USB_KICK`, `XYF_SMALL_CRC`, `__bfin__`), sanitized programs (`FORK_EACH`: one sanitizer start for many
  runs, `tests/fork_each.py`), the host tools (mod_link, mod_assemble, tfw_extract; `XYF_SANITIZE=1` picks their
  sanitized builds) and the simulator's test variants (`xy_forever/sim.py` `VARIANTS`).
- **Generated sources** (`hosttest/generate.py`): the simulator's rewritten device sources, its defines per variant,
  cold.cpp's ranges for the host tools; each written only when its content changes.
- **Another simulator variant** (`--version`, `--crash-trigger`) is built on first use by CMake into `build/sim/`
  (`hosttest/sim/`), incrementally, one folder per variant.

`test-changed` selects a test again when the C++ it used changed: a module, doctest file or program reports its
sources to `tests/xyf_select.py`, which follows their includes.
