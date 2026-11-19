"""Clean-startup readiness is bounded, retries only busy, and preserves older peers."""
import unittest

from xy_forever.conformance_connection import StartupNotReady, wait_startup_ready
from xy_forever import usb_proto_ids as ids


def hello(with_space=True):
    return 'XYF1;proto=1.21;cmds=%032x' % ((1 << ids.STORAGE_SPACE) if with_space else 2)


class StartupReadyTest(unittest.TestCase):
    def test_only_busy_retries_with_the_remaining_deadline(self):
        replies = iter([ids.S_BUSY, ids.S_BUSY, ids.S_OK])
        ticks = iter([0.0, 0.1, 0.2, 0.3])
        bounds = []
        def probe(remaining):
            bounds.append(remaining)
            return next(replies)
        wait_startup_ready(hello(), probe, timeout=1.0, clock=lambda: next(ticks))
        self.assertEqual(bounds, [0.9, 0.8, 0.7])

    def test_non_busy_error_fails_immediately(self):
        calls = []
        for code in (ids.S_LINK, ids.S_STORAGE, ids.S_UNSUPPORTED):
            def probe(remaining):
                calls.append(code)
                return code
            with self.assertRaisesRegex(StartupNotReady, 'storage.space: '):
                wait_startup_ready(hello(), probe)
        self.assertEqual(calls, [ids.S_LINK, ids.S_STORAGE, ids.S_UNSUPPORTED])

    def test_permanently_busy_peer_hits_one_deadline(self):
        ticks = iter([0.0, 0.1, 0.9, 1.1])
        calls = []
        def probe(remaining):
            calls.append(remaining)
            return ids.S_BUSY
        with self.assertRaisesRegex(StartupNotReady, 'deadline'):
            wait_startup_ready(hello(), probe, timeout=1.0, clock=lambda: next(ticks))
        self.assertEqual(len(calls), 2)

    def test_transport_failure_is_not_retried(self):
        def broken(remaining):
            raise OSError('disconnected')
        with self.assertRaisesRegex(OSError, 'disconnected'):
            wait_startup_ready(hello(), broken)

    def test_older_peers_send_no_probe(self):
        def forbidden(remaining):
            self.fail('older peer received a readiness probe')
        for text in (hello(False), 'XYF1;proto=1'):
            wait_startup_ready(text, forbidden)
