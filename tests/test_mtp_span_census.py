"""scripts/mtp_span_census.py: peeks only the probe's listed spans, retries a busy port, reports words."""
import importlib.util
import subprocess
import tempfile
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('mtp_span_census', ROOT / 'scripts/mtp_span_census.py')
C = importlib.util.module_from_spec(spec)
spec.loader.exec_module(C)


class FakeImage:
    def read(self, a, n):
        return bytes((a + i) & 0xff for i in range(n))


class FakePeek:
    """Stands in for `xyf_host.py peek`: busy `busy` times, then writes the image with `patch` applied."""
    def __init__(self, patch=None, busy=0):
        self.patch, self.busy, self.calls = patch or {}, busy, []

    def __call__(self, cmd, **_):
        self.calls.append(cmd)
        if self.busy:
            self.busy -= 1
            return subprocess.CompletedProcess(cmd, 1, '', 'BlockingIOError: [Errno 35] Resource temporarily unavailable')
        a, n, out = int(cmd[-4], 16), int(cmd[-3], 16), Path(cmd[-1])
        data = bytearray(FakeImage().read(a, n))
        for at, word in self.patch.items():
            if a <= at < a + n:
                data[at - a:at - a + 4] = word.to_bytes(4, 'little')
        out.write_bytes(bytes(data))
        return subprocess.CompletedProcess(cmd, 0, '', '')


class CensusTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='xyf-spancensus-')
        self.out = Path(self.tmp.name)
        (self.out / 'peeks').mkdir()
        patcher = mock.patch.object(C.time, 'sleep')
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def test_order_matches_probe_start(self):
        service = (ROOT / 'target/usb_service.cpp').read_text()
        gate = (ROOT / 'target/mtp_usb_gate.hpp').read_text()       # 186-003: the boot-time identity() result,
        self.assertIn('return gate::refusal<crc_at>(menu_usb == 2u && !usb_off, engine, spans,', service)   # then engine, probe
        self.assertLess(gate.index('return check_usb;'), gate.index('return check_engine;'))
        self.assertLess(gate.index('return check_engine;'), gate.index('return check_probe;'))
        self.assertEqual([c[1] for c in C.CHECKS], ['identity()', 'engine_identity()', 'probe_spans()'])
        rows = C.spans()
        self.assertEqual(len(rows), len(C.B.usb_spans()) + len(C.B.mtp_spans()) + len(C.B.mtp_usb_spans()))
        self.assertTrue(all(C.peekable(a, n) for _, _, _, a, n, _ in rows))

    def test_refuses_unlisted_range(self):
        census = C.Census(self.out, 5, [(0x88a13460, 0x344)], runner=FakePeek(), log=lambda s: None)
        for a, n in ((0x88a13460, 0x348), (0x88a13464, 0x340), (0x88000000, 4)):
            with self.assertRaises(SystemExit):
                census.peek(a, n)
        self.assertEqual(len(census.peek(0x88a13460, 0x344)), 0x344)

    def test_retries_busy_port_then_reports_words(self):
        peek = FakePeek(patch={0x88a137a0: 0x00000001}, busy=2)
        rows = [('usb', 'identity()', 65, 0x88a13460, 0x344, 0), ('probe', 'probe_spans()', 0, 0x88a0c0b0, 0x80,
                                                                    C.zlib.crc32(FakeImage().read(0x88a0c0b0, 0x80)))]
        lines = []
        census = C.Census(self.out, 5, [(r[3], r[4]) for r in rows], runner=peek, log=lines.append)
        report = C.run(rows, FakeImage(), census, lines.append)
        self.assertEqual(len(peek.calls), 4)                          # two busy, then one per span
        self.assertIn('    0x88a137a0 image 0xa3a2a1a0 device 0x00000001', lines)
        self.assertIn('probe #0 0x88a0c0b0 + 0x80: equal', lines)
        usb, probe = report['checks']
        self.assertEqual(usb['differing'][0]['words'], [['0x88a137a0', '0xa3a2a1a0', '0x00000001']])
        self.assertEqual(probe['verdict'], 'passes')

    def test_hard_failure_is_not_retried(self):
        def fail(cmd, **_):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 13, 'peek: range at 0x88a13460', '')
        calls = []
        census = C.Census(self.out, 5, [(0x88a13460, 0x344)], runner=fail, log=lambda s: None)
        with self.assertRaises(SystemExit):
            census.peek(0x88a13460, 0x344)
        self.assertEqual(len(calls), 1)


if __name__ == '__main__':
    unittest.main()
