"""Opt-in clean-startup precondition for fixtures that require completed background recovery.

HELLO establishes a protocol session. It does not promise completion of recovery scans.
Keep startup/fault observations on raw connections; this probe is not a global idle lease.
"""

import time

from xy_forever import usb_proto as P
from xy_forever import usb_proto_ids as ids


class StartupNotReady(RuntimeError):
    pass


def wait_startup_ready(hello, probe, timeout=5.0, clock=time.monotonic):
    """Probe storage.space v1 until ok; only busy may retry, within one deadline.

    probe(remaining_seconds) returns the status; its transport must honor the remaining bound.
    No staging or mutation is retried. This is a startup precondition, not a global idle lease.
    """
    peer = P.Hello(hello)
    if peer.pre_v1 or not peer.answers(ids.STORAGE_SPACE):
        return
    deadline = clock() + timeout
    while True:
        remaining = deadline - clock()
        if remaining <= 0:
            raise StartupNotReady('startup readiness: storage.space stayed busy until the deadline')
        status = probe(remaining)
        if status == ids.S_OK:
            return
        if status != ids.S_BUSY:
            raise StartupNotReady('startup readiness: storage.space: %s' % P.status_name(status))
