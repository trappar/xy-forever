#!/usr/bin/env python3
"""Watch whether the OP-XY's USB interrupt still answers (host only, read-only).

usage: python3 scripts/usb_alive.py [--seconds N] [--every S]

Every S seconds, reads the manufacturer and product string descriptors of the first OP-XY (VID 0x2367)
with a 500 ms control transfer. B's USB stack answers it inside the USB interrupt
without any storage call, so the first failure after successes timestamps a freeze. Build 120's watchdog report mode
puts b's stuck address in those strings.
macOS caches strings read with the device's langid 0x0409, so this asks with 0x0407.
Claims no interface (OpenMTP / the CDC port are unaffected). Needs Homebrew libusb.
"""
import argparse
import ctypes
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mtp_probe import DeviceDescriptor, OP_XY_VID, load   # noqa: E402


def product(lib, ctx):
    devs = ctypes.POINTER(ctypes.c_void_p)()
    n = lib.libusb_get_device_list(ctx, ctypes.byref(devs))
    try:
        for i in range(n):
            d = DeviceDescriptor()
            lib.libusb_get_device_descriptor(ctypes.c_void_p(devs[i]), ctypes.byref(d))
            if d.idVendor != OP_XY_VID:
                continue
            handle = ctypes.c_void_p()
            if lib.libusb_open(ctypes.c_void_p(devs[i]), ctypes.byref(handle)):
                return 'open failed'
            texts = []
            for index in (d.iManufacturer, d.iProduct):
                buf = ctypes.create_string_buffer(255)
                # GET_DESCRIPTOR(string, langid 0x0407), 500 ms: macOS answers 0x0409 (the
                # device's own language) from its cache; any other langid reaches the device.
                rc = lib.libusb_control_transfer(handle, ctypes.c_uint8(0x80), ctypes.c_uint8(6),
                                                 ctypes.c_uint16(0x0300 | index), ctypes.c_uint16(0x0407),
                                                 buf, ctypes.c_uint16(255), ctypes.c_uint(500))
                if rc < 2:
                    lib.libusb_close(handle)
                    return 'NO ANSWER (%d)' % rc
                texts.append(buf.raw[2:rc].decode('utf-16-le', 'replace'))
            lib.libusb_close(handle)
            return 'ok ' + ' | '.join(texts)
        return 'absent'
    finally:
        lib.libusb_free_device_list(devs, 1)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--seconds', type=float, default=60)
    ap.add_argument('--every', type=float, default=2)
    args = ap.parse_args(argv)
    lib = load()
    ctx = ctypes.c_void_p()
    if lib.libusb_init(ctypes.byref(ctx)):
        sys.exit('libusb_init failed')
    end = time.time() + args.seconds
    last = None
    while time.time() < end:
        state = product(lib, ctx)
        if state != last:
            print('%s %s' % (time.strftime('%T'), state), flush=True)
            last = state
        time.sleep(args.every)
    return 0


if __name__ == '__main__':
    sys.exit(main())
