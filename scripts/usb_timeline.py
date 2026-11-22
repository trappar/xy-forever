#!/usr/bin/env python3
"""Log when OP-XY USB devices appear and disappear (host clock, ~20 ms resolution).

    python3 scripts/usb_timeline.py [--seconds N] > timeline.txt

Used for boot timing without touching the device: with the OP-XY off but on USB
power, the stock bootloader shows its charging-loop mass-storage device ("OP-XY
Disk", interface class 8). It disappears when power is pressed; the OS's
own USB device (or XY Forever's CDC service) appears later. The difference is the
power-on-to-USB time, measured the same way for stock and XY Forever builds.
Read-only: it only lists the macOS USB registry (ioreg); nothing is sent.
"""

import argparse
import re
import subprocess
import sys
import time

VENDOR = 9063  # 0x2367 teenage engineering


def devices():
    """Set of (product, pid, interface classes) for the TE device(s) on the bus."""
    out = subprocess.run(['ioreg', '-p', 'IOUSB', '-l', '-w0'], capture_output=True, text=True).stdout
    names = set()
    for block in out.split('+-o ')[1:]:
        if '"idVendor" = %d' % VENDOR in block:
            name = re.search(r'"USB Product Name" = "([^"]*)"', block)
            pid = re.search(r'"idProduct" = (\d+)', block)
            names.add((name.group(1) if name else '?', int(pid.group(1)) if pid else -1))
    if not names:
        return set()
    # Interfaces are IOUSBHostInterface objects (service plane), not in the IOUSB plane.
    iface = subprocess.run(
        ['ioreg', '-r', '-c', 'IOUSBHostInterface', '-l', '-w0'], capture_output=True, text=True
    ).stdout
    classes = set()
    for block in iface.split('+-o ')[1:]:
        if '"idVendor" = %d' % VENDOR in block or '"idVendor"=%d' % VENDOR in block:
            classes.update(re.findall(r'"bInterfaceClass" ?= ?(\d+)', block))
    return {(name, pid, tuple(sorted(classes))) for name, pid in names}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--seconds', type=float, default=3600)
    parser.add_argument('--interval', type=float, default=0.02)
    args = parser.parse_args()
    start = time.time()
    last = devices()
    print('%s start %s' % (time.strftime('%H:%M:%S'), sorted(last) or 'none'), flush=True)
    while time.time() - start < args.seconds:
        now = devices()
        t = time.time()
        for d in sorted(last - now):
            print(
                '%s %.3f - %s pid %d class %s' % (time.strftime('%H:%M:%S'), t, d[0], d[1], ','.join(d[2])), flush=True
            )
        for d in sorted(now - last):
            print(
                '%s %.3f + %s pid %d class %s' % (time.strftime('%H:%M:%S'), t, d[0], d[1], ','.join(d[2])), flush=True
            )
        last = now
        time.sleep(args.interval)
    return 0


if __name__ == '__main__':
    sys.exit(main())
