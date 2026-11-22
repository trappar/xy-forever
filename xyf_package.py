"""Shim: `uv run xyf_package.py ...` runs `uv run xyf-package ...` (xy_forever/xyf_package.py)."""
import sys

from xy_forever.xyf_package import main

if __name__ == '__main__':
    sys.exit(main())
