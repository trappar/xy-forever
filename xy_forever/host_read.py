"""Read a file through existing read-only serial commands."""

import sys
from pathlib import Path

from . import usb_proto as P


def configure(sub):
    read = sub.add_parser('read')
    read.add_argument('path')
    read.add_argument('--out', type=Path)
    read.add_argument('--all', action='store_true', help='any size (ranged reads; needs the Test 038 service)')
    read.add_argument('--offset', type=int, help='one ranged read (up to READ_RANGE bytes) at this offset')


def read(dev, args):
    code, body = (
        dev.read_all(args.path)
        if args.all
        else dev.read_file(args.path, args.offset)
        if args.offset is not None
        else dev.read_file(args.path)
    )
    if code:
        print('read failed: %s' % P.status_name(code))
        return code
    if args.out:
        args.out.write_bytes(body)
    else:
        sys.stdout.write(body.decode('utf-8', 'replace'))
    return 0
