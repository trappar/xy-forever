"""The conformance suite (plan 1.4): protocol/v1.conformance.json validates against the spec and
every case is interpretable; the validator refuses bad data; the device guard refuses what could
change a real device beyond the test area; scripts/conformance.py runs every case against the
simulator (and the pre-v1 variant), and prints one NEED line when nothing answers. The TypeScript
runner of the same file: tests/test_ts_client.py. No device access: every run uses --sim."""
import contextlib
import copy
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
import conformance as script                          # noqa: E402  (scripts/conformance.py)
import xyf_host as host                               # noqa: E402
from xy_forever import conformance as C, paths, sim   # noqa: E402
from xy_forever import usb_proto as P                 # noqa: E402

UNAVAILABLE = sim.available() or (None if sim.STOCK_OS.is_file() else 'private stock 1.2.1 os.ldr absent')
PRE_V1 = 'XYF1;service=usb;role=b;proto=1;install=stock-88a06a7e'
BUILD = paths.newest_verified_build()                    # a verified product build, when present
AREA = '/yaffs2/xy-forever/log/conformance/'


def data():
    return C.load()


def spec():
    return C.load_spec()


def case(d, name):
    return next(c for c in d['cases'] if c['name'] == name)


class CaseFileTest(unittest.TestCase):
    def test_every_case_validates_and_is_interpreted(self):
        d = data()
        self.assertEqual(C.check(spec(), d), len(d['cases']))
        self.assertGreaterEqual(len(d['cases']), 50)
        self.assertEqual(d['protocol'], spec()['protocol']['version'])

    def test_validator_rejects_bad_cases(self):
        def broken(change, expect):
            d = data()
            change(d)
            with self.assertRaises(C.CaseError) as ctx:
                C.validate(spec(), d)
            self.assertIn(expect, str(ctx.exception))

        broken(lambda d: case(d, 'hello/reply').update(extra=1), 'unknown key')
        broken(lambda d: case(d, 'hello/reply')['steps'][0].update(bogus=1), 'unknown step key')
        broken(lambda d: case(d, 'hello/reply')['steps'][0]['expect'].update(status='nope'), 'unknown status')
        broken(lambda d: case(d, 'file.read/not-found')['steps'][0].update(send='diag.peek'), 'not a published')
        broken(lambda d: case(d, 'file.read/not-found')['steps'][0]['fields'].update(size=1), 'unknown field')
        broken(lambda d: case(d, 'file.read/not-found')['steps'][0]['expect'].update(fields={'nope': 1}), 'no field')
        broken(lambda d: case(d, 'hello/repeat').update(name='hello/repeat '), 'group/what')
        broken(lambda d: d['cases'].append(copy.deepcopy(case(d, 'hello/repeat'))), 'duplicate name')
        broken(lambda d: case(d, 'hello/repeat').update(effects=['restarts']), 'but the steps do')
        broken(lambda d: case(d, 'service.hold/on-off').pop('why_not'), 'why_not is required')
        broken(lambda d: case(d, 'stage/max-payload').update(real_device=True) or
               case(d, 'stage/max-payload').pop('why_not'), 'device guard refuses')
        # A real-device case that sends what the guard refuses: set-default, the OS, writes outside the area.
        for name, step in (('library.set_default', {'id': 'opxy-121'}), ('device.reboot', {'target': 1}),
                           ('service.hold', {'on': 1}), ('menu.continue', {'mode': 1})):
            broken(lambda d, name=name, step=step: case(d, 'hello/repeat')['steps'].append(
                {'send': name, 'fields': step, 'expect': {'status': 'bad_request'}}), 'device guard refuses')
        broken(lambda d: case(d, 'file.write_staged/sha')['steps'][1]['fields'].update(
            sha256={'sha256': 'tiny'}, path='/yaffs2/xy-forever/state-a.txt'), 'device guard refuses')
        broken(lambda d: case(d, 'file.write_staged/utf8-name')['steps'].append({'write': {
            'path': '/yaffs2/xy-forever/x.txt', 'data': {'blob': 'tiny'}}}), 'only into the test area')
        broken(lambda d: case(d, 'hello/reply')['steps'][0]['expect']['hello']['answers'].pop(), 'every command')
        # Coverage: a documented error nobody shows, a stale uncovered entry, a stream nobody sends.
        broken(lambda d: d['uncovered'].pop(1), 'file.read link: no case expects it')
        broken(lambda d: d['uncovered'].append({'command': 'file.read', 'status': 'not_found', 'why': 'x'}),
               'is shown by a case')
        broken(lambda d: d['cases'].remove(case(d, 'frame/bad-crc')), "stream 'a bad CRC, then a good frame'")
        broken(lambda d: case(d, 'hello/before-hello').pop('rules') and
               case(d, 'stage/lost-on-restart').pop('rules') and case(d, 'device.reboot/to-the-menu').pop('rules'),
               'rule hello_first')
        # Both runners must interpret every value the same way: a cstr above its layout's max_bytes.
        broken(lambda d: case(d, 'file.read/not-found')['steps'][0]['fields'].update(
            path={'prefix': AREA, 'fill': 'a', 'length': 240}), 'above 240 bytes')

    def test_protogen_validates_the_case_file(self):
        import protogen
        d = data()
        case(d, 'hello/reply')['steps'][0]['expect']['status'] = 'nope'
        with self.assertRaisesRegex(protogen.SpecError, 'v1.conformance.json'):
            protogen.generate(protogen.load(), d)


class DeviceGuardTest(unittest.TestCase):
    def guard(self, name, payload):
        s = C.Spec(spec())
        kind = s.type_of(name) if isinstance(name, str) else name
        return C.device_guard(s, kind, payload, AREA)

    def test_allows_reads_staging_area_writes_and_menu_restarts(self):
        u32 = lambda v: struct.pack('<I', v)                                          # noqa: E731
        for name, payload in (('hello', b''), ('file.read', b'/yaffs2/x\0'), ('screen.read', u32(0)),
                              ('stage.begin', bytes(40)), ('stage.data', bytes(5)), ('stage.end', b''),
                              ('device.reboot', b''), ('device.reboot', u32(2)), ('device.reboot', u32(0)),
                              ('device.reboot', b'\1\0'), ('menu.continue', u32(2)), ('menu.continue', bytes(5)),
                              ('service.hold', u32(2)), ('service.hold', b''), (0x06, b''),
                              ('file.write_staged', (AREA + 'a/b.txt\0').encode() + bytes(32)),
                              ('file.write_staged', b'/yaffs2/x\0' + C.UNSAFE_TOKEN),
                              ('file.write_staged', b'/yaffs2/xy-forever/state-a.txt\0' + bytes(31))):
            self.assertIsNone(self.guard(name, payload), (name, payload))

    def test_refuses_what_changes_more(self):
        u32 = lambda v: struct.pack('<I', v)                                          # noqa: E731
        for name, payload in (('boot.install', bytes(32)), ('boot.uninstall', bytes(32)),
                              ('library.set_default', b'opxy-121\0'), ('device.reboot', u32(1)),
                              ('menu.continue', b''), ('menu.continue', u32(0)), ('menu.continue', u32(1)),
                              ('service.hold', u32(1)), ('service.hold', u32(0)), (0x05, bytes(8)),
                              (0x33, u32(0)), (0x34, bytes(8)), (0x60, b'HANG'), (0x81, b''),
                              ('stage.data', bytes(4 + 1025)),
                              ('file.write_staged', b'/yaffs2/xy-forever/state-a.txt\0' + bytes(32)),
                              ('file.write_staged', (AREA + '../x.txt\0').encode() + bytes(32)),
                              ('file.write_staged', (AREA + '\0').encode() + bytes(32))):
            self.assertIsNotNone(self.guard(name, payload), (name, payload))

    def test_link_refuses_before_sending(self):
        class Port:
            sent = []

            def write(self, data):
                self.sent.append(data)
        port = Port()
        link = C.Link(port, C.Spec(spec()), lambda line: None, guard_area=AREA)
        with self.assertRaises(C.Unsafe):
            link.write(P.encode(0x40, 1, b'opxy-121\0'))
        with self.assertRaises(C.Unsafe):                      # inside raw bytes too
            link.write(b'\0' * 7 + P.encode(0x31, 2, b''))
        self.assertEqual(port.sent, [])
        link.write(P.encode(0x01, 3, b''))
        self.assertEqual(len(port.sent), 1)

    def test_real_device_runs_only_safe_cases(self):
        d = data()
        runner = C.Runner(spec(), d, target=None, real=True)
        runner.hello_text = 'XYF1;proto=1.1;cmds=%032x' % 2
        for c in d['cases']:
            why = runner.why_skip(c)
            if not c['real_device']:
                self.assertTrue(why.startswith('not on a real device'), c['name'])
            self.assertFalse(set(c['effects']) - set(C.DEVICE_EFFECTS) and c['real_device'], c['name'])


class FakeDevice:
    """A transport that ignores the first frame (a port that appeared before its service reads)
    and then answers hello; it records every request type it got."""
    TEXT = b'XYF1;service=usb;role=b;proto=1.1;cmds=%032x' % (1 << 1 | 1 << 0x40)

    def __init__(self):
        self.parser, self.got, self.out = P.Parser(), [], b''

    def write(self, data):
        for kind, seq, _ in self.parser.feed(data):
            self.got.append(kind)
            if kind == 0x01 and len(self.got) > 1:
                self.out += P.encode(0x81, seq, bytes(4) + self.TEXT)

    def read(self, timeout):
        data, self.out = self.out, b''
        if not data:
            time.sleep(min(timeout, 0.01))
        return data

    def close(self):
        pass


class FakeTarget:
    def __init__(self):
        self.device = FakeDevice()

    def present(self):
        return True

    def open(self):
        return self.device


class RealDevicePathTest(unittest.TestCase):
    """The real-device code path without a device: HELLO retried as xyf_host.connect does, and
    the guard stopping a frame a case should never send (nothing reaches the port)."""

    def test_hello_retry_and_runtime_guard(self):
        target = FakeTarget()
        runner = C.Runner(spec(), data(), target, real=True, reply_cap=1.0)
        self.assertEqual(runner.start(3.0), FakeDevice.TEXT.decode())
        self.assertEqual(target.device.got, [0x01, 0x01])
        bad = dict(name='x/unsafe', doc='x', effects=[], real_device=True, steps=[
            {'send': 'library.set_default', 'fields': {'id': 'opxy-121'}, 'expect': {'status': 'ok'}}])
        result, detail = runner.run_case(bad)
        self.assertEqual(result, 'FAIL')
        self.assertIn('refused by the device guard', detail)
        self.assertNotIn(0x40, target.device.got)


def run_script(argv):
    out, err = io.StringIO(), io.StringIO()
    paths.SESSIONS.mkdir(parents=True, exist_ok=True)
    sessions = tempfile.TemporaryDirectory(prefix='conformance-test-', dir=paths.SESSIONS)   # evidence: private only
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = script.main(argv, sessions=sessions.name)
        folders = list(Path(sessions.name).iterdir())
        summary = json.loads((folders[0] / 'summary.json').read_text()) if folders else None
    finally:
        host.SIM = None
        sessions.cleanup()
    return code, out.getvalue(), summary


@unittest.skipIf(UNAVAILABLE, UNAVAILABLE)
class SimulatorRunTest(unittest.TestCase):
    def simulator(self, tmp, hello=None):
        root = sim.seed(Path(tmp) / 'yaffs2')
        return sim.Simulator(root, Path(tmp) / 'bootpart.bin', build_dir=sim.build(hello=hello),
                             log=Path(tmp) / 'log.txt')

    def test_every_case_against_the_simulator(self):
        argv = ['--fixture', 'xyf_build=%s' % (BUILD / 'candidate.ldr')] if BUILD else []
        with tempfile.TemporaryDirectory(prefix='xyf-conf-') as tmp, self.simulator(tmp) as running:
            code, out, summary = run_script(['--sim', running.address, '--keep'] + argv)
        self.assertEqual(code, 0, out)
        rows = {r['case']: r for r in summary['cases']}
        self.assertEqual(len(rows), len(data()['cases']))
        skipped = {n for n, r in rows.items() if r['result'] == 'SKIP'}
        self.assertEqual(skipped, {'hello/pre-v1'} | (set() if argv else {'boot.install/build'}), out)
        self.assertNotIn('FAIL', out)

    def test_pre_v1_variant(self):
        with tempfile.TemporaryDirectory(prefix='xyf-conf-') as tmp, self.simulator(tmp, PRE_V1) as running:
            code, out, summary = run_script(['--sim', running.address, '--keep', '--only', '^(hello|file.read)/'])
        self.assertEqual(code, 0, out)
        results = {r['case']: r['result'] for r in summary['cases']}
        self.assertEqual(results.pop('hello/pre-v1'), 'PASS')
        self.assertEqual(set(results.values()), {'SKIP'})                  # v1 cases: not this device

    def test_evidence_folder_kept_only_on_failure_or_keep(self):
        """--sim: the session folder is removed when nothing failed, unless --keep; a FAIL keeps it."""
        only = ['--only', '^file.read/ranged-and-whole$']
        with tempfile.TemporaryDirectory(prefix='xyf-conf-') as tmp, self.simulator(tmp) as running:
            code, out, summary = run_script(['--sim', running.address] + only)
            self.assertEqual((code, summary), (0, None), out)
            self.assertRegex(out.splitlines()[-1], r'^folder: .*-sim-conformance-1 removed \(simulator run')
            code, out, summary = run_script(['--sim', running.address, '--keep'] + only)
            self.assertEqual((code, summary['passed']), (0, 1), out)
            (Path(tmp) / 'yaffs2' / 'firmware.version').unlink()       # the case reads it: not_found, FAIL
            code, out, summary = run_script(['--sim', running.address] + only)
            self.assertEqual((code, summary['failed']), (script.EXIT_FAIL, 1), out)

    def test_need_when_nothing_answers(self):
        port = sim.free_port()
        code, out, summary = run_script(['--sim', '127.0.0.1:%d' % port, '--wait', '0.3'])
        self.assertEqual(code, script.EXIT_NEED)
        self.assertEqual([line for line in out.splitlines() if line], [line for line in out.splitlines()
                                                                       if line.startswith('NEED: ')])
        self.assertEqual(len(out.splitlines()), 1)
        self.assertIsNone(summary)                                         # no session folder

    def test_check_and_plan_touch_nothing(self):
        code, out, summary = run_script(['--check'])
        self.assertEqual((code, out.strip(), summary), (0, 'ok %d cases interpreted' % len(data()['cases']), None))
        code, out, _ = run_script(['--plan', '--sim', '127.0.0.1:1'])
        self.assertEqual(code, 0)
        self.assertEqual(len(out.splitlines()), len(data()['cases']))


if __name__ == '__main__':
    unittest.main()
