"""Shim: `uv run xyf_build.py ...` runs `uv run xyf-build ...` (xy_forever/xyf_build.py)."""

import sys

from xy_forever.xyf_build import main

if __name__ == '__main__':
    sys.exit(main())
