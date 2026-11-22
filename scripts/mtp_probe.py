#!/usr/bin/env python3
"""Minimal MTP/PTP probe of the OP-XY over libusb (read-only; host only).

usage: uv run scripts/mtp_probe.py [--timeout-ms N] [--list [--names [--get NAME]]]

Opens the first OP-XY (VID 0x2367) interface of class 255 or 6 with bulk IN/OUT
endpoints, then sends OpenSession, GetDeviceInfo, GetStorageIDs,
GetStorageInfo and (with --list) GetObjectHandles for the root, printing each
phase as it arrives so a missing answer names the request. Nothing is written
to the device's storage. Quit OpenMTP / Image Capture first: only one program
can claim the interface. Needs Homebrew libusb (ctypes, no Python packages).
The session and transport are xy_forever/mtp_read.py's (shared with setup.py
import-stock --from-device), which refuses every operation that is not a read.
"""

import argparse
import struct
import sys
import zlib

from xy_forever import mtp_read
from xy_forever.mtp_read import OP_XY_VID, DeviceDescriptor, MtpError, ptp_string  # noqa: F401
from xy_forever.mtp_read import READ_ONLY as NAMES


def load():
    """libusb, or exit with one line (scripts/usb_alive.py uses it too)."""
    try:
        return mtp_read.load_libusb()
    except MtpError as e:
        sys.exit(str(e))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--timeout-ms', type=int, default=5000)
    ap.add_argument('--list', action='store_true', help='also GetObjectHandles of the root')
    ap.add_argument('--names', action='store_true', help='with --list: GetObjectInfo of each root object')
    ap.add_argument('--get', metavar='NAME', help='with --names: GetObject of this root file (printed as size + CRC)')
    args = ap.parse_args(argv)
    try:
        transport = mtp_read.open_op_xy(args.timeout_ms, log=print)
    except MtpError as e:
        sys.exit(str(e))
    session = mtp_read.Session(transport)

    def transact(code, *params, quiet=False):
        name = NAMES[code]
        try:
            rcode, payload = session.transact(code, *params)
        except MtpError as e:
            print(str(e))
            return None
        if payload and not quiet:
            print('%s: data %d bytes' % (name, len(payload)))
        if not quiet or rcode != 0x2001:
            print('%s: response 0x%04x%s' % (name, rcode, ' (OK)' if rcode == 0x2001 else ''))
        return payload

    transact(0x1002, 1)  # OpenSession
    info = transact(0x1001)  # GetDeviceInfo
    ids = transact(0x1004)  # GetStorageIDs
    storages = (
        list(struct.unpack_from('<%dI' % struct.unpack_from('<I', ids)[0], ids, 4)) if ids and len(ids) >= 4 else []
    )
    print('storages', ['0x%08x' % s for s in storages])
    for s in storages[:1]:
        transact(0x1005, s)  # GetStorageInfo
        if args.list:
            handles = transact(0x1007, s, 0, 0xFFFFFFFF)  # GetObjectHandles (root)
            if handles and len(handles) >= 4:
                count = struct.unpack_from('<I', handles)[0]
                print('root objects', count)
                for h in struct.unpack_from('<%dI' % count, handles, 4) if args.names else ():
                    info = transact(0x1008, h, quiet=True)
                    if not info or len(info) < 53:
                        print('  0x%08x: no info' % h)
                        continue
                    fmt, size = struct.unpack_from('<H', info, 4)[0], struct.unpack_from('<I', info, 8)[0]
                    name, _ = ptp_string(info, 52)
                    print('  0x%08x %s %10d  %s' % (h, 'dir ' if fmt == 0x3001 else 'file', size, name))
                    if args.get and name == args.get:
                        body = transact(0x1009, h)
                        if body is not None:
                            print('  got %d bytes, crc32 %08x' % (len(body), zlib.crc32(body)))
    transact(0x1003)  # CloseSession
    transport.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
