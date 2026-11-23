"""Reserve the OP-XY while running a non-interactive command or session script."""

import argparse
import math
import os
import subprocess
import sys
import uuid

from xy_forever import device_lock, paths, procgroup

# The group leader watches the lock owner. If the owner is killed, the kernel drops
# its lease and the watcher ends the remaining commands in that process group.
CHILD = """
import os, subprocess, sys
from xy_forever import procgroup
procgroup.end_group_with_parent()
if os.getppid() != int(sys.argv[1]):
    sys.exit("device session owner exited")
env = dict(os.environ)
key = sys.argv[2]
env[key] += ":" + str(os.getpgrp())
try:
    result = subprocess.call(sys.argv[3:], env=env)
except OSError as error:
    sys.exit(str(error))
sys.exit(result if result >= 0 else 128 - result)
"""


def run(command, name, wait, timeout):
    if os.environ.get(device_lock.SESSION_ENV):
        raise SystemExit('already running under a device lease')
    session = uuid.uuid4().hex
    if not device_lock.lock(paths.DEVICE_LOCK, wait, what='lease: ' + name, session=session):
        raise SystemExit('device lease needs a private folder (run mise run setup)')
    try:
        child = procgroup.popen(
            [sys.executable, '-c', CHILD, str(os.getpid()), device_lock.SESSION_ENV, *command],
            env={**os.environ, device_lock.SESSION_ENV: session},
            stdin=subprocess.DEVNULL,
        )
        try:
            result = child.wait(timeout=timeout)
            return result if result >= 0 else 128 - result
        except subprocess.TimeoutExpired:
            print('device lease expired after %g s (%s)' % (timeout, name), file=sys.stderr)
            return 124
        finally:
            procgroup.kill_group(child)
    finally:
        device_lock.release()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--name', help='session name shown to other agents (defaults to the command name)')
    parser.add_argument('--wait', type=float, default=0.0, help='seconds to wait for another holder (default: 0)')
    parser.add_argument(
        '--timeout', type=float, default=900.0, help='maximum session duration in seconds (default: 900)'
    )
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command:
        parser.error('specify a command after --')
    if not math.isfinite(args.wait) or args.wait < 0:
        parser.error('--wait must be finite and nonnegative')
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error('--timeout must be finite and positive')
    procgroup.tie_to_parent()
    try:
        return run(command, args.name or os.path.basename(command[0]), args.wait, args.timeout)
    except KeyboardInterrupt:
        return 130


if __name__ == '__main__':
    sys.exit(main())
