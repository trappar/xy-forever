"""Tests never open a real OP-XY (docs/operating/build-install.md, "Checks").

With XYF_NO_DEVICE set, every opener of a real device raises DeviceRefused before it looks for one: the serial
port (xyf_host.find_port's glob, transport.SerialPort), USB MTP (mtp_read.open_op_xy, scripts/usb_alive.py) and
pyusb (scripts/mtp_usb_probe.py, scripts/mtp_files_test.py). The simulator (TCP) and pseudo-terminals stay open.
tests/conftest.py and scripts/check.py set it, and so does importing xy_forever in a process that has loaded
unittest, so every test and every script a test runs as a subprocess inherits it. There is no way to turn it off
in a test: a test fakes the opener instead.
"""

import os
import re
import sys

ENV = 'XYF_NO_DEVICE'
PTY = re.compile(r'/dev/(ttys|pts/)\d')  # a pseudo-terminal (macOS, Linux): a test's fake tty, never a device


class DeviceRefused(BaseException):
    """Not an Exception: no `except Exception` on the way (a retry, a skip, a failed step) can hide it."""


def on():
    """True in a test run."""
    return bool(os.environ.get(ENV)) or 'unittest' in sys.modules


def switch_on():
    """The guard for this process and every process it starts."""
    os.environ[ENV] = '1'


def refuse(what):
    """DeviceRefused naming what a test tried to open, when the guard is on."""
    if on():
        raise DeviceRefused('tests may not open a real device: %s' % what)


def refuse_tty(path):
    """refuse() for a tty path that is not a pseudo-terminal."""
    if not PTY.match(path):
        refuse(path)
