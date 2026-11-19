"""Opt-in clean-startup fixtures and the conformance runner's connection setup.

HELLO establishes the protocol session, not completion of background recovery. Only tests
that require clean startup call this barrier, before their measured requests. Older peers
without storage.space keep their original exchanges. Split from conformance.py (source budget).
"""
import struct
import time

from xy_forever import usb_proto as P, usb_proto_ids as ids


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


class ConformanceConnection:
    def open(self, hello=True, wait=None, ready=False):
        from xy_forever.conformance import Fail, Link
        wait = self.timing['back_ms'] / 1000.0 if wait is None else wait
        deadline = time.monotonic() + wait
        while True:
            while not self.target.present():
                if time.monotonic() > deadline:
                    raise Fail('the port did not come back within %.1f s' % wait)
                time.sleep(0.05)
            last = None
            while True:
                try:
                    link = Link(self.target.open(), self.spec, self.log, self.data['test_area'] if self.real else None)
                    break
                except OSError as error:
                    last = error
                    if time.monotonic() > deadline:
                        raise Fail('the port did not open: %s' % last)
                    time.sleep(0.1)
            self.log('# connected')
            if not (hello or self.real):
                return link
            try:
                self.greet(link, hello, max(deadline, time.monotonic() + self.cap('hello')))
            except (Fail, OSError) as error:
                # A port found while it was still going down (the restart had not reached it yet) drops
                # during the greeting: connect again within the same time limit.
                if not (link.closed or isinstance(error, OSError)) or time.monotonic() > deadline:
                    raise
                link.close()
                self.log('# the connection dropped during the greeting: connecting again')
                time.sleep(0.1)
                continue
            if ready:
                def probe(remaining):
                    body, _ = link.request(ids.STORAGE_SPACE, struct.pack('<I', 1), remaining)
                    return struct.unpack_from('<I', body)[0]
                try:
                    wait_startup_ready(self.hello_text, probe, self.reply_cap)
                except Exception as error:
                    link.close()
                    if isinstance(error, StartupNotReady):
                        raise Fail(str(error)) from error
                    raise
            return link
