#!/usr/bin/env python3
"""Minimal MTP/PTP probe of the OP-XY over libusb (read-only; host only).

usage: python3 scripts/mtp_probe.py [--timeout-ms N] [--list [--names [--get NAME]]]

Opens the first OP-XY (VID 0x2367) interface of class 255/6 with bulk IN/OUT
endpoints, then sends OpenSession, GetDeviceInfo, GetStorageIDs,
GetStorageInfo and (with --list) GetObjectHandles for the root, printing each
phase as it arrives so a missing answer names the request. Nothing is written
to the device's storage. Quit OpenMTP / Image Capture first: only one program
can claim the interface. Needs Homebrew libusb (ctypes, no Python packages).
"""
import argparse
import ctypes
import ctypes.util
import struct
import subprocess
import sys
import zlib

OP_XY_VID = 0x2367
NAMES = {0x1001: 'GetDeviceInfo', 0x1002: 'OpenSession', 0x1003: 'CloseSession', 0x1004: 'GetStorageIDs',
         0x1005: 'GetStorageInfo', 0x1007: 'GetObjectHandles', 0x1008: 'GetObjectInfo', 0x1009: 'GetObject'}


def ptp_string(data, at):
    n = data[at]
    return data[at + 1:at + 1 + 2 * n].decode('utf-16-le', 'replace').rstrip('\0'), at + 1 + 2 * n


def load():
    try:
        prefix = subprocess.run(['brew', '--prefix', 'libusb'], capture_output=True, text=True).stdout.strip()
    except OSError:
        prefix = ''
    for path in ([prefix + '/lib/libusb-1.0.dylib'] if prefix else []) + [ctypes.util.find_library('usb-1.0') or '']:
        if path:
            try:
                return ctypes.CDLL(path)
            except OSError:
                pass
    sys.exit('libusb-1.0 not found (brew install libusb)')


class DeviceDescriptor(ctypes.Structure):
    _fields_ = [('bLength', ctypes.c_uint8), ('bDescriptorType', ctypes.c_uint8), ('bcdUSB', ctypes.c_uint16),
                ('bDeviceClass', ctypes.c_uint8), ('bDeviceSubClass', ctypes.c_uint8),
                ('bDeviceProtocol', ctypes.c_uint8), ('bMaxPacketSize0', ctypes.c_uint8),
                ('idVendor', ctypes.c_uint16), ('idProduct', ctypes.c_uint16), ('bcdDevice', ctypes.c_uint16),
                ('iManufacturer', ctypes.c_uint8), ('iProduct', ctypes.c_uint8),
                ('iSerialNumber', ctypes.c_uint8), ('bNumConfigurations', ctypes.c_uint8)]


class Endpoint(ctypes.Structure):
    _fields_ = [('bLength', ctypes.c_uint8), ('bDescriptorType', ctypes.c_uint8),
                ('bEndpointAddress', ctypes.c_uint8), ('bmAttributes', ctypes.c_uint8),
                ('wMaxPacketSize', ctypes.c_uint16), ('bInterval', ctypes.c_uint8),
                ('bRefresh', ctypes.c_uint8), ('bSynchAddress', ctypes.c_uint8),
                ('extra', ctypes.c_void_p), ('extra_length', ctypes.c_int)]


class AltSetting(ctypes.Structure):
    _fields_ = [('bLength', ctypes.c_uint8), ('bDescriptorType', ctypes.c_uint8),
                ('bInterfaceNumber', ctypes.c_uint8), ('bAlternateSetting', ctypes.c_uint8),
                ('bNumEndpoints', ctypes.c_uint8), ('bInterfaceClass', ctypes.c_uint8),
                ('bInterfaceSubClass', ctypes.c_uint8), ('bInterfaceProtocol', ctypes.c_uint8),
                ('iInterface', ctypes.c_uint8), ('endpoint', ctypes.POINTER(Endpoint)),
                ('extra', ctypes.c_void_p), ('extra_length', ctypes.c_int)]


class Interface(ctypes.Structure):
    _fields_ = [('altsetting', ctypes.POINTER(AltSetting)), ('num_altsetting', ctypes.c_int)]


class Config(ctypes.Structure):
    _fields_ = [('bLength', ctypes.c_uint8), ('bDescriptorType', ctypes.c_uint8),
                ('wTotalLength', ctypes.c_uint16), ('bNumInterfaces', ctypes.c_uint8),
                ('bConfigurationValue', ctypes.c_uint8), ('iConfiguration', ctypes.c_uint8),
                ('bmAttributes', ctypes.c_uint8), ('MaxPower', ctypes.c_uint8),
                ('interface', ctypes.POINTER(Interface)), ('extra', ctypes.c_void_p), ('extra_length', ctypes.c_int)]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--timeout-ms', type=int, default=5000)
    ap.add_argument('--list', action='store_true', help='also GetObjectHandles of the root')
    ap.add_argument('--names', action='store_true', help='with --list: GetObjectInfo of each root object')
    ap.add_argument('--get', metavar='NAME', help='with --names: GetObject of this root file (printed as size + CRC)')
    args = ap.parse_args(argv)
    lib = load()
    ctx = ctypes.c_void_p()
    if lib.libusb_init(ctypes.byref(ctx)):
        sys.exit('libusb_init failed')
    devs = ctypes.POINTER(ctypes.c_void_p)()
    n = lib.libusb_get_device_list(ctx, ctypes.byref(devs))
    target = None
    for i in range(n):
        d = DeviceDescriptor()
        lib.libusb_get_device_descriptor(ctypes.c_void_p(devs[i]), ctypes.byref(d))
        if d.idVendor == OP_XY_VID:
            target = (ctypes.c_void_p(devs[i]), d)
            break
    if not target:
        sys.exit('no OP-XY (VID 0x2367) on USB')
    dev, desc = target
    print('device %04x:%04x' % (desc.idVendor, desc.idProduct))
    cfg = ctypes.POINTER(Config)()
    lib.libusb_get_active_config_descriptor(dev, ctypes.byref(cfg))
    found = None
    for i in range(cfg.contents.bNumInterfaces):
        alt = cfg.contents.interface[i].altsetting[0]
        eps = [alt.endpoint[k] for k in range(alt.bNumEndpoints)]
        print('interface %d class %d/%d/%d endpoints %s' % (alt.bInterfaceNumber, alt.bInterfaceClass,
              alt.bInterfaceSubClass, alt.bInterfaceProtocol,
              ', '.join('%02x/%d/%d' % (e.bEndpointAddress, e.bmAttributes & 3, e.wMaxPacketSize) for e in eps)))
        bulk_in = [e.bEndpointAddress for e in eps if e.bmAttributes & 3 == 2 and e.bEndpointAddress & 0x80]
        bulk_out = [e.bEndpointAddress for e in eps if e.bmAttributes & 3 == 2 and not e.bEndpointAddress & 0x80]
        if bulk_in and bulk_out and not found:
            found = (alt.bInterfaceNumber, bulk_in[0], bulk_out[0])
    if not found:
        sys.exit('no interface with bulk IN and OUT')
    number, ep_in, ep_out = found
    handle = ctypes.c_void_p()
    rc = lib.libusb_open(dev, ctypes.byref(handle))
    if rc:
        sys.exit('libusb_open failed: %d' % rc)
    rc = lib.libusb_claim_interface(handle, number)
    if rc:
        sys.exit('claim interface %d failed: %d (quit OpenMTP / Image Capture and retry)' % (number, rc))
    tid = [0]

    def bulk(ep, data=None, size=0):
        buf = ctypes.create_string_buffer(data if data is not None else size, len(data) if data is not None else size)
        moved = ctypes.c_int()
        rc = lib.libusb_bulk_transfer(handle, ctypes.c_uint8(ep), buf, len(buf.raw), ctypes.byref(moved),
                                      ctypes.c_uint(args.timeout_ms))
        return rc, buf.raw[:moved.value]

    def transact(code, *params, quiet=False):
        tid[0] += 1
        name = NAMES.get(code, '0x%04x' % code)
        cmd = struct.pack('<IHHI', 12 + 4 * len(params), 1, code, tid[0]) + b''.join(struct.pack('<I', p) for p in params)
        rc, _ = bulk(ep_out, cmd)
        if rc:
            print('%s: command NOT sent (libusb %d)' % (name, rc)); return None
        payload = b''
        while True:
            rc, got = bulk(ep_in, size=0x10000)
            if rc:
                print('%s: NO ANSWER (libusb %d after %d ms)' % (name, rc, args.timeout_ms)); return None
            if len(got) < 12:
                print('%s: short container %r' % (name, got)); return None
            length, kind, rcode, rtid = struct.unpack_from('<IHHI', got)
            if kind == 2:
                payload = got[12:]
                while len(payload) < length - 12:
                    rc, more = bulk(ep_in, size=0x10000)
                    if rc:
                        print('%s: data stopped at %d/%d bytes' % (name, len(payload), length - 12)); return None
                    payload += more
                if not quiet:
                    print('%s: data %d bytes' % (name, len(payload)))
                continue
            if not quiet or rcode != 0x2001:
                print('%s: response 0x%04x%s' % (name, rcode, ' (OK)' if rcode == 0x2001 else ''))
            return payload

    transact(0x1002, 1)                                  # OpenSession
    info = transact(0x1001)                              # GetDeviceInfo
    ids = transact(0x1004)                               # GetStorageIDs
    storages = list(struct.unpack_from('<%dI' % struct.unpack_from('<I', ids)[0], ids, 4)) if ids and len(ids) >= 4 else []
    print('storages', ['0x%08x' % s for s in storages])
    for s in storages[:1]:
        transact(0x1005, s)                              # GetStorageInfo
        if args.list:
            handles = transact(0x1007, s, 0, 0xffffffff)  # GetObjectHandles (root)
            if handles and len(handles) >= 4:
                count = struct.unpack_from('<I', handles)[0]
                print('root objects', count)
                for h in (struct.unpack_from('<%dI' % count, handles, 4) if args.names else ()):
                    info = transact(0x1008, h, quiet=True)
                    if not info or len(info) < 53:
                        print('  0x%08x: no info' % h); continue
                    fmt, size = struct.unpack_from('<H', info, 4)[0], struct.unpack_from('<I', info, 8)[0]
                    name, _ = ptp_string(info, 52)
                    print('  0x%08x %s %10d  %s' % (h, 'dir ' if fmt == 0x3001 else 'file', size, name))
                    if args.get and name == args.get:
                        body = transact(0x1009, h)
                        if body is not None:
                            print('  got %d bytes, crc32 %08x' % (len(body), zlib.crc32(body)))
    transact(0x1003)                                     # CloseSession
    lib.libusb_release_interface(handle, number)
    lib.libusb_close(handle)
    return 0


if __name__ == '__main__':
    sys.exit(main())
