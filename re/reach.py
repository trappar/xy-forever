"""Reachable DDR functions of the 1.2.1 bootloader from entry points (direct calls).

    python3 re/reach.py ADDR... > reach.txt      lines "<start> <end>", then "external ..."
Output feeds re/spans.py.
"""

import sys
from pathlib import Path

import bootloader


def reach(roots):
    seen, ends, external = set(), {}, set()
    stack = list(roots)
    while stack:
        a = stack.pop()
        if a in seen:
            continue
        seen.add(a)
        b = bootloader.body(int(a, 16))
        if not b:
            continue
        ends[a] = b[-1].split()[0]
        for c in ('%08x' % c for c in bootloader.calls_of(b)):
            (stack.append(c) if c.startswith(('88', '889', '0803')) else external.add(c))
    return ends, external


if __name__ == '__main__':
    ends, external = reach(sys.argv[1:])
    for a in sorted(ends):
        print(a, ends[a])
    print('external', ' '.join(sorted(external)))
