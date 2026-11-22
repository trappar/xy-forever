"""#7's read-only diagnostic and actual host runner. Simulator/fakes only; never a device."""
import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest
import zlib


from xy_forever import usb_proto_ids as P

ROOT = Path(__file__).resolve().parents[1]

spec = importlib.util.spec_from_file_location('boot_probe_script', ROOT / 'scripts/boot_probe.py')
B = importlib.util.module_from_spec(spec)
spec.loader.exec_module(B)


class Fake:
    def __init__(self, replies):
        self.replies, self.calls, self.hello_info = iter(replies), [], object()

    def call(self, kind, payload, timeout):
        self.calls.append((kind, struct.unpack_from('<I', payload)[0], timeout))
        r = next(self.replies)
        if isinstance(r, Exception):
            raise r
        return r


class RunnerTests(unittest.TestCase):
    def test_recommendation_depends_on_final_restoration(self):
        image = b'abc'
        good = struct.pack('<IIIBB', 3, len(image), zlib.crc32(image), 1, 0).ljust(512, b'\0')
        for end_status in (P.S_OK, P.S_STORAGE):
            dev = Fake([(0, struct.pack('<III', 1, 0, 0)), (0, struct.pack('<I', 1) + good),
                        (0, struct.pack('<I', 1) + bytes(512)),
                        (0, struct.pack('<I', 1) + image.ljust(512, b'\0')),
                        (end_status, struct.pack('<I', 1))])
            with tempfile.TemporaryDirectory() as tmp:
                result = B.run(dev, Path(tmp))
            self.assertEqual(result['go'], end_status == P.S_OK)
            self.assertEqual(result['recommendation'].startswith('go '), end_status == P.S_OK)
            self.assertEqual([x[1] for x in dev.calls], [B.BEGIN, B.HEADER, B.HEADER, B.SECTOR, B.END])

    def test_newer_reply_is_refused_and_transport_timeout_sends_no_recovery(self):
        with self.assertRaisesRegex(B.ProbeError, 'version'):
            B.call(Fake([(0, struct.pack('<I', 2))]), B.BEGIN)
        dev = Fake([(0, struct.pack('<III', 1, 0, 0)), TimeoutError('no answer')])
        with tempfile.TemporaryDirectory() as tmp:
            result = B.run(dev, Path(tmp))
        self.assertTrue(result['link_lost'])
        self.assertFalse(result['restored'])
        self.assertEqual([x[1] for x in dev.calls], [B.BEGIN, B.HEADER])

    def test_responsive_read_error_restores_but_link_error_stops(self):
        for error, expected in ((P.S_STORAGE, [B.BEGIN, B.HEADER, B.END]),
                                (P.S_LINK, [B.BEGIN, B.HEADER])):
            dev = Fake([(0, struct.pack('<III', 1, 0, 0)), (error, struct.pack('<I', 1)),
                        (0, struct.pack('<I', 1))])
            with tempfile.TemporaryDirectory() as tmp:
                result = B.run(dev, Path(tmp))
            self.assertEqual([x[1] for x in dev.calls], expected)
            self.assertEqual(result['restored'], error == P.S_STORAGE)
            self.assertFalse(result['go'])

