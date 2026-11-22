"""Build time (hosttest/CMakeLists.txt, hosttest/sim/CMakeLists.txt): the C++ files the build generates.

usage:
  generate.py sim-lists ROOT OUT.cmake        the simulator's source names (xy_forever/sim_sources.py), as CMake lists
  generate.py sim-sources ROOT OUT            the simulator's rewritten sources (xy_forever/sim.py write_sources)
  generate.py sim-defines ROOT VARIANT OUT.h  a simulator variant's defines (sim.VARIANTS name, or JSON options)
  generate.py rewrite ROOT SOURCE OUT         one source rewritten as the simulator rewrites it
Every output is written only when its content changes: ninja then recompiles only what an edit touched.
"""

import json
import sys
from pathlib import Path


def write(path, data):
    path = Path(path)
    data = data if isinstance(data, bytes) else data.encode()
    try:
        if path.read_bytes() == data:
            return
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def main(argv):
    command, root = argv[0], Path(argv[1]).resolve()
    sys.path.insert(0, str(root))
    from xy_forever import sim, sim_sources

    if command == 'sim-lists':
        lists = dict(
            XYF_SIM_TARGET=sim_sources.TARGET_SOURCES,
            XYF_SIM_NATIVE=sim_sources.NATIVE_SOURCES,
            XYF_SIM_HOST=sim_sources.SIM_SOURCES,
            XYF_SIM_VARIANTS=tuple(sim.VARIANTS),
        )
        write(argv[2], ''.join('set(%s %s)\n' % (name, ';'.join(values)) for name, values in lists.items()))
    elif command == 'sim-sources':
        sim.write_sources(argv[2], write)
    elif command == 'sim-defines':
        variant = argv[2]
        options = json.loads(variant) if variant.startswith('{') else sim.VARIANTS[variant]
        lines = []
        for flag in sim.product_defines(**options):
            name, _, value = flag[len('-D') :].partition('=')
            lines.append('#define %s %s\n' % (name, value or '1'))
        write(argv[3], ''.join(lines))
    elif command == 'rewrite':
        source = Path(argv[2])
        write(argv[3], sim.rewrite(source.name, source.read_text()))
    else:
        raise SystemExit(__doc__)


if __name__ == '__main__':
    main(sys.argv[1:])
