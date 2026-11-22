"""Opt-in clean-startup fixtures and the conformance runner's connection setup.

HELLO establishes the protocol session, not completion of background recovery. Only tests
that require clean startup call this barrier, before their measured requests. Older peers
without storage.space keep their original exchanges. Split from conformance.py (source budget).
"""
import struct
import time

from xy_forever import usb_proto_ids as ids
from xy_forever.startup_ready import StartupNotReady, wait_startup_ready


class ConformanceConnection:
    def open(self, hello=True, wait=None, ready=False):
        from xy_forever.conformance import Fail, Link
        wait = self.timing['back_ms'] / 1000.0 if wait is None else wait
        deadline = time.monotonic() + wait
        while True:
            while not self.target.present():
                if time.monotonic() > deadline:
                    raise Fail('the port did not come back within %.1f s' % wait)
                getattr(self.target, 'pause', time.sleep)(0.05)
            last = None
            while True:
                try:
                    link = Link(self.target.open(), self.spec, self.log, self.data['test_area'] if self.real else None)
                    break
                except OSError as error:
                    last = error
                    if time.monotonic() > deadline:
                        raise Fail('the port did not open: %s' % last)
                    getattr(self.target, 'pause', time.sleep)(0.1)
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
                getattr(self.target, 'pause', time.sleep)(0.1)
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
