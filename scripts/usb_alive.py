#!/usr/bin/env python3
"""Watch whether the OP-XY's USB interrupt still answers (host only, read-only).

usage: uv run scripts/usb_alive.py [--seconds N] [--every S]

Every S seconds, reads the manufacturer and product string descriptors of the first OP-XY (VID 0x2367)
with a 500 ms control transfer. B's USB stack answers it inside the USB interrupt
without any storage call, so the first failure after successes timestamps a freeze. Build 120's watchdog report mode
puts b's stuck address in those strings.
macOS caches strings read with the device's langid 0x0409, so this asks with 0x0407.
Claims no interface (OpenMTP / the CDC port are unaffected). Needs Homebrew libusb (through pyusb).
"""

import argparse
import sys
import time

from xy_forever import device_lock, mtp_read, no_device


def load():
    """pyusb's libusb backend, or exit with one line."""
    try:
        return mtp_read.load_libusb()
    except mtp_read.MtpError as e:
        sys.exit(str(e))


def product(backend):
    import usb.core
    import usb.util

    dev = usb.core.find(idVendor=mtp_read.OP_XY_VID, backend=backend)
    if dev is None:
        return 'absent'
    dev.default_timeout = 500
    try:
        # GET_DESCRIPTOR(string, langid 0x0407), 500 ms: macOS answers 0x0409 (the
        # device's own language) from its cache; any other langid reaches the device.
        return 'ok ' + ' | '.join(
            usb.util.get_string(dev, index, 0x0407) for index in (dev.iManufacturer, dev.iProduct)
        )
    except (usb.core.USBError, ValueError) as error:
        return 'NO ANSWER (%s)' % error
    finally:
        usb.util.dispose_resources(dev)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--seconds', type=float, default=60)
    ap.add_argument('--every', type=float, default=2)
    args = ap.parse_args(argv)
    no_device.refuse('USB (libusb)')
    device_lock.hold()
    backend = load()
    end = time.time() + args.seconds
    last = None
    while time.time() < end:
        state = product(backend)
        if state != last:
            print('%s %s' % (time.strftime('%T'), state), flush=True)
            last = state
        time.sleep(args.every)
    return 0


if __name__ == '__main__':
    sys.exit(main())
