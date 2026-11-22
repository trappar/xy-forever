"""Child processes that never outlive what started them (#133).

Each child runs in its own session (so its own process group), and the whole group ends with it:
run() is subprocess.run for node, pnpm and the like (on a timeout, a failure or a normal end the
group gets SIGTERM, then SIGKILL after GRACE seconds), so a test runner's children (simulators
among them) end with the command. tie_to_parent() makes a long-running wrapper process
(`python3 -m xy_forever.sim`) end, through SIGTERM, when the process that started it is gone,
even when that one was killed outright; its own cleanup then ends its group (kill_group).
"""
import os
import signal
import subprocess
import threading
import time

GRACE = 3.0                      # s between SIGTERM and SIGKILL


def popen(argv, **kwargs):
    """subprocess.Popen in a new session: the child leads its own process group."""
    return subprocess.Popen(argv, start_new_session=True, **kwargs)


def _signal_group(pgid, sig):
    try:
        os.killpg(pgid, sig)
        return True
    except OSError:              # no process left in the group
        return False


def kill_group(process, grace=GRACE):
    """Ends process's whole group (it leads one: popen()): SIGTERM, SIGKILL after grace s; reaps it."""
    pgid = process.pid
    if _signal_group(pgid, signal.SIGTERM):
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline:
            process.poll()       # reap the leader: a zombie still counts as a member
            if not _signal_group(pgid, 0):
                break
            time.sleep(0.05)
        else:
            _signal_group(pgid, signal.SIGKILL)
    try:
        process.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        pass


def _drain(process):
    """The rest of the output of an ended group (a process outside it may still hold a pipe)."""
    try:
        return process.communicate(timeout=GRACE)
    except subprocess.TimeoutExpired:
        return None, None


def run(argv, timeout=None, input=None, capture_output=False, **kwargs):
    """subprocess.run(argv, ...) in its own process group, which ends with it however the call ends,
    and when the command itself ends (what it left running would hold its output pipes open). A
    timeout raises subprocess.TimeoutExpired, as subprocess.run does, with what output came."""
    if capture_output:
        kwargs['stdout'] = kwargs['stderr'] = subprocess.PIPE
    if input is not None:
        kwargs['stdin'] = subprocess.PIPE
    process = popen(argv, **kwargs)
    deadline = None if timeout is None else time.monotonic() + timeout
    try:
        while True:
            step = 0.5 if deadline is None else max(0.0, min(0.5, deadline - time.monotonic()))
            try:
                out, err = process.communicate(input, timeout=step)
                return subprocess.CompletedProcess(argv, process.returncode, out, err)
            except subprocess.TimeoutExpired:
                input = None                          # sent once
            if process.poll() is not None:
                kill_group(process)
                return subprocess.CompletedProcess(argv, process.returncode, *_drain(process))
            if deadline is not None and time.monotonic() >= deadline:
                kill_group(process)
                raise subprocess.TimeoutExpired(argv, timeout, *_drain(process))
    finally:
        kill_group(process)      # anything the command left running in its group


def end_group_with_parent(poll=0.5):
    """For a group leader (started by popen()): a daemon thread SIGKILLs its whole group, this process
    included, once its parent is gone (a test runner interrupted or killed: its workers end with it)."""
    parent = os.getppid()

    def watch():
        while os.getppid() == parent:
            time.sleep(poll)
        os.killpg(os.getpgid(0), signal.SIGKILL)
    threading.Thread(target=watch, name='parent-watch', daemon=True).start()


def tie_to_parent(poll=0.5):
    """For a process's __main__: SIGTERM and SIGHUP raise SystemExit (so finally blocks run, once),
    and a daemon thread sends this process SIGTERM when its parent is gone (re-parented)."""
    def ending(sig, frame):
        for s in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
            signal.signal(s, signal.SIG_IGN)          # the cleanup runs once, uninterrupted
        raise SystemExit(128 + sig)
    for s in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(s, ending)
    parent = os.getppid()

    def watch():
        while os.getppid() == parent:
            time.sleep(poll)
        os.kill(os.getpid(), signal.SIGTERM)
    threading.Thread(target=watch, name='parent-watch', daemon=True).start()
