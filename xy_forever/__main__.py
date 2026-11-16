import argparse
import json
from pathlib import Path
import sys

from .ldr import MAX_IMAGE, inspect_ldr
from .validation import validate_package
from .xyf_entry import make_entry

from .paths import PRIVATE_ROOT, private_output   # noqa: F401 (re-exported)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Offline XY Forever tools; no device access')
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('inspect-ldr', 'inspect-package'):
        commands.add_parser(name).add_argument('path', type=Path)
    entry = commands.add_parser('library-entry', help='Prepare an XY Forever library entry under private/')
    entry.add_argument('ldr', type=Path)
    entry.add_argument('--out', required=True, type=Path)
    entry.add_argument('--id', required=True)
    entry.add_argument('--name', required=True)
    entry.add_argument('--version', default='')
    entry.add_argument('--kind', default='custom', choices=('official', 'open-xy', 'custom'))
    entry.add_argument('--layout', type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == 'inspect-ldr':
            if args.path.stat().st_size > MAX_IMAGE:
                raise ValueError('LDR exceeds 16 MiB')
            result = inspect_ldr(args.path.read_bytes())
        elif args.command == 'inspect-package':
            _, result = validate_package(args.path)
        else:
            result = make_entry(args.ldr, private_output(args.out), args.id, args.name, args.version, args.kind,
                                args.layout)
        print(json.dumps(result, indent=2))
        return 0
    except (ValueError, OSError) as error:
        print(f'error: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
