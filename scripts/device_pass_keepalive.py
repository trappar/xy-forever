"""The device pass's scripts/keepalive.sh handling (split out of device_pass.py, which re-exports it)."""

import contextlib
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from xy_forever import device_lock

ROOT = Path(__file__).resolve().parents[1]
EXIT_NEED = 3  # device_pass.EXIT_NEED
KEEPALIVE_NEED = 'NEED: keepalive.sh pid %d still runs: kill it (its folder %s), then rerun'
SHELLS = ('sh', 'bash', 'zsh', 'dash', 'ksh')


class Keepalive:
    """Handoff to and from scripts/keepalive.sh after a device pass."""

    def running(self):
        try:
            pids = subprocess.run(['pgrep', '-f', 'keepalive.sh'], capture_output=True, text=True).stdout.split()
        except OSError:
            return []
        mine, pid = set(), os.getpid()
        while pid > 1 and pid not in mine:  # this script and its ancestors (the shell that ran it)
            mine.add(pid)
            pid = int(subprocess.run(['ps', '-o', 'ppid=', '-p', str(pid)], capture_output=True, text=True).stdout or 0)
        found = []
        for pid in set(map(int, pids)) - mine:
            args = subprocess.run(['ps', '-o', 'args=', '-p', str(pid)], capture_output=True, text=True).stdout.split()
            statedir = keepalive_dir(args)
            if statedir:
                found.append((pid, statedir))
        return found

    def stop_running(self, wait=8.0):
        """Stops every keepalive.sh loop: its stop file, then (still running after wait s) SIGTERM to its status
        call and to it. One that outlives that still holds the port: its NEED line, then SystemExit(EXIT_NEED)."""
        # A different session may be running its own foreground keepalive. Take
        # access before looking for loops we can stop.
        device_lock.hold(wait)
        stopped = []
        for pid, statedir in self.running():
            print(
                '[%s] stopping keepalive.sh pid %d (%s)' % (time.strftime('%H:%M:%S'), pid, statedir),
                file=sys.stderr,
                flush=True,
            )
            with contextlib.suppress(OSError):
                (Path(statedir) / 'stop-keepalive').touch()
            deadline = time.monotonic() + wait
            while time.monotonic() < deadline and alive(pid):
                time.sleep(0.2)
            if alive(pid):  # stuck in a status call (read-only): end it
                subprocess.run(['pkill', '-TERM', '-P', str(pid)], capture_output=True)
                with contextlib.suppress(OSError):
                    os.kill(pid, signal.SIGTERM)
                deadline = time.monotonic() + 2.0
                while time.monotonic() < deadline and alive(pid):
                    time.sleep(0.2)
            if alive(pid):
                print(KEEPALIVE_NEED % (pid, statedir), flush=True)
                raise SystemExit(EXIT_NEED)
            stopped.append(dict(pid=pid, statedir=statedir))
        return stopped

    def start(self, folder):
        """Start a standalone keepalive after the final port closes. A lease owns its own handoff."""
        if os.environ.get(device_lock.SESSION_ENV):
            return None
        device_lock.release()
        with open(folder / 'keepalive.log', 'ab') as log:
            proc = subprocess.Popen(
                ['/bin/sh', str(ROOT / 'scripts/keepalive.sh'), str(folder)],
                cwd=ROOT,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        (folder / 'keepalive.pid').write_text('%d\n' % proc.pid)
        return proc.pid


def keepalive_dir(args):
    """STATEDIR when ps args are a keepalive.sh loop (`sh .../keepalive.sh DIR`, as Keepalive.start runs it, or
    `bash -c .../keepalive.sh DIR`), else None. A command line that only mentions keepalive.sh (a grep filter on
    this script's output) is none: stopping one killed the script's own pipeline, silently (#50)."""
    i = (2 if args[1:2] == ['-c'] else 1) if args and Path(args[0]).name.lstrip('-') in SHELLS else 0
    if len(args) > i + 1 and args[i].endswith('keepalive.sh') and Path(args[i + 1]).is_dir():
        return args[i + 1]
    return None


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False
