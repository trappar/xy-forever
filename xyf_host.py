"""Shim: `uv run xyf_host.py ...` runs `uv run xyf-host ...` (xy_forever/xyf_host.py)."""

import sys

from xy_forever.xyf_host import main

if __name__ == '__main__':
    sys.exit(main())
