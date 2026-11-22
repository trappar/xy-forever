"""One host process at a time talks to the OP-XY: an OS file lock (fcntl.flock) on <private>/xy-forever/device.lock.

Every opener of the real device takes it before it looks for one: xyf_host.connect (with its timeout) and the serial
port (xy_forever/transport.py) and USB MTP (mtp_read.open_op_xy, scripts/usb_alive.py). It is held until the process exits: the kernel drops it then,
even on a crash or a kill, so a lock is never left behind. A second process waits up to its timeout, then stops with
"device busy" naming the holder, never a collision on the port. The simulator (TCP) and pseudo-terminals take no lock.
With no private folder (nothing set up), there is nothing to coordinate and no lock is taken; nor in a test run
(xy_forever/no_device.py), unless the test names its own lock file.
"""

import datetime
import fcntl
import os
import sys
import time

from xy_forever import no_device, paths

_held = {}  # lock path: the open file holding the lock (kept for the life of the process)


class DeviceBusy(SystemExit):
    """Another process holds the device. A SystemExit: a step's `except Exception` cannot turn it into a retry."""


def holder(path):
    """What the holder wrote into the lock file (pid, command, time), or '' when unreadable."""
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ''


def hold(wait=0.0, path=None, root=None):
    """Takes the device lock for this process (again: nothing to do). Waits up to wait seconds for another holder,
    saying so once on stderr; then raises DeviceBusy. Returns the lock path, or None when there is no private
    folder."""
    if path is None and no_device.on():
        return None
    return lock(path or paths.DEVICE_LOCK, wait, root)


def lock(
    path,
    wait=0.0,
    root=None,
    what=None,
    waiting='device busy: %(who)s is talking to the OP-XY; waiting up to %(wait)g s\n',
):
    """An flock on path, held until this process exits (taken again: nothing to do); also scripts/check.py's lock.
    Waits up to wait seconds (None: as long as it takes) for another holder, writing waiting once on stderr; then
    raises DeviceBusy. The file records this process: its pid, what (default: its command) and the time. Returns
    the lock path, or None when root (default the private folder) is missing."""
    path = str(path)
    if path in _held:
        return path
    if not os.path.isdir(str(root or paths.PRIVATE_ROOT)):
        return None
    os.makedirs(os.path.dirname(path), exist_ok=True)
    f = open(path, 'a+')
    deadline, said = time.monotonic() + (wait or 0.0), False
    while True:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            who = holder(path) or 'another process'
            if wait is not None and time.monotonic() >= deadline:
                f.close()
                raise DeviceBusy('device busy: %s is talking to the OP-XY (%s); try again when it ends' % (who, path))
            if not said:
                sys.stderr.write(waiting % dict(who=who, wait=wait or 0.0))
                sys.stderr.flush()
                said = True
            time.sleep(0.2)
    f.seek(0)
    f.truncate()
    what = what or ' '.join([os.path.basename(sys.argv[0] if sys.argv else '') or 'python'] + sys.argv[1:3])
    f.write('pid %d (%s) since %s\n' % (os.getpid(), what, datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
    f.flush()
    _held[path] = f
    return path
