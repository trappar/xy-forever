"""Exclusive host access to the OP-XY, via flock in <private>/xy-forever/.

device.lock is held by a standalone tool or by device_session for a whole session. A session's commands
prove membership against that lock and use device.command.lock to serialize individual access. Standalone
tools also take the command lock, so they cannot race an in-flight command if a session owner dies.
Locks last until explicit handoff or process exit. The simulator, pseudo-terminals and tests skip them
unless a test supplies its own path.
"""

import datetime
import fcntl
import os
import sys
import time
from pathlib import Path

from xy_forever import no_device, paths

SESSION_ENV = 'XYF_DEVICE_SESSION'
_held = {}  # lock path: the process's open file holding the lock


class DeviceBusy(SystemExit):
    """Another process holds the device. A SystemExit: a step's `except Exception` cannot turn it into a retry."""


def holder(path):
    """The holder's public record (pid, command, time), or '' when unreadable."""
    try:
        with open(path) as f:
            return f.readline().strip()
    except OSError:
        return ''


def hold(wait=0.0, path=None, root=None):
    """Takes exclusive access for this process, within one wait budget."""
    if path is None and no_device.on():
        return None
    path = Path(path or paths.DEVICE_LOCK)
    command = path.with_suffix('.command.lock')
    if str(command) in _held:
        return str(path)

    session = os.environ.get(SESSION_ENV)
    deadline = time.monotonic() + wait
    took_gate = not session and str(path) not in _held
    try:
        if not session and not lock(path, wait, root):
            return None
        if not lock(command, max(0.0, deadline - time.monotonic()), root):
            raise DeviceBusy('device lock folder disappeared; check the private folder before continuing')
        if session and not _active_session(path, session):
            raise DeviceBusy('device session ended; start a new lease before talking to the OP-XY')
    except BaseException:
        _release(command)
        if took_gate:
            _release(path)
        raise
    return str(path)


def _active_session(path, session):
    token, _, group = session.partition(':')
    if group != str(os.getpgrp()):
        return False
    try:
        with path.open('r+') as f:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                f.readline()  # public holder record
                return f.readline().strip() == token
    except FileNotFoundError:
        pass
    return False


def _release(path):
    f = _held.pop(str(path), None)
    if f is not None:
        f.close()


def release(path=None):
    """Hand off after closing the last device connection."""
    path = Path(path or paths.DEVICE_LOCK)
    _release(path.with_suffix('.command.lock'))
    _release(path)


def lock(
    path,
    wait=0.0,
    root=None,
    what=None,
    waiting='device busy: %(who)s is talking to the OP-XY; waiting up to %(wait)g s\n',
    session=None,
):
    """An flock on path, held until release or process exit (taken again: nothing to do).
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
    try:
        deadline, said = time.monotonic() + (wait or 0.0), False
        while True:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                who = holder(path) or 'another process'
                left = deadline - time.monotonic()
                if wait is not None and left <= 0:
                    raise DeviceBusy(
                        'device busy: %s is talking to the OP-XY (%s); try again when it ends' % (who, path)
                    )
                if not said:
                    sys.stderr.write(waiting % dict(who=who, wait=wait or 0.0))
                    sys.stderr.flush()
                    said = True
                time.sleep(0.2 if wait is None else min(0.2, left))
        f.seek(0)
        f.truncate()
        what = what or ' '.join([os.path.basename(sys.argv[0] if sys.argv else '') or 'python'] + sys.argv[1:3])
        stamp = datetime.datetime.now().astimezone().isoformat(timespec='seconds')
        f.write('pid %d (%s) since %s\n' % (os.getpid(), ' '.join(what.splitlines()), stamp))
        if session:
            f.write(session + '\n')
        f.flush()
    except BaseException:
        f.close()
        raise
    _held[path] = f
    return path
