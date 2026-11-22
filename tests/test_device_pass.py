"""scripts/device_pass.py driven by a fake device (no serial port, no device)."""

import contextlib
import hashlib
import importlib.util
import io
import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('device_pass', ROOT / 'scripts/device_pass.py')
dp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dp)

BASE = '/yaffs2/xy-forever/'
P = dp.P
PRE_V1_HELLO = 'XYF1;service=usb;role=b;proto=1;install=stock-88a06a7e'
IMAGE = 'f458e3cc'  # the installed build's image (self.build)
V1_HELLO = P.hello_text('0.1.0', IMAGE, 'product', dp.xyf_build.usb_switches('xyf-product'))
PROTOCOL_STEPS = [name for name, _, _ in dict(dp.STAGES)['protocol']]


def catalog_blob(files):
    """The menu's catalog (target/xyf_catalog.cpp encode) of the firmware/<id>/info.txt files."""
    ids = sorted({p[len(BASE + 'firmware/') :].split('/')[0] for p in files if p.startswith(BASE + 'firmware/')})
    out = struct.pack('<4I', 0x31435958, len(ids), 0xFFFFFFFF, 0)
    for i in ids:
        info = dict(
            line.split('=', 1)
            for line in files.get(BASE + 'firmware/%s/info.txt' % i, b'').decode().splitlines()
            if '=' in line
        )
        image = BASE + 'firmware/%s/os.ldr' % i in files
        content = 'generation' in info  # content not installed
        flags = (8 if content else 1) if image else 0
        out += (
            i.encode().ljust(32, b'\0')
            + info.get('name', i).encode().ljust(48, b'\0')
            + info.get('version', '').encode().ljust(32, b'\0')
            + bytes((0, flags, 0, 0))
        )
    return out + struct.pack('<I', zlib.crc32(out))


def with_121(dev):
    """An opxy-121 entry beside opxy-121-stock: usable, content installed (official): set-default-alt's choice."""
    dev.files.update(
        {BASE + 'firmware/opxy-121/info.txt': b'name=OP-XY 1.2.1\n', BASE + 'firmware/opxy-121/os.ldr': b'x'}
    )
    return dev


def slot(default, seq):
    body = ('default=%s\nseq=%d\n' % (default, seq)).encode()
    return body + b'crc32=%08x\n' % (zlib.crc32(body) & 0xFFFFFFFF)


def screen_png(screen, row):
    """A screenshot as xyf_host makes it (480 x 216 RGB, filter none): black, the screen counter in the first
    pixel, the selected home row's white bar (device_pass.row_selected)."""
    rows = []
    for y in range(216):
        line = bytearray(480 * 3)
        if y == 0:
            line[0:2] = bytes((screen & 0xFF, screen >> 8 & 0xFF))
        if row is not None and dp.ROW_TOP + dp.ROW_PITCH * row <= y < dp.ROW_TOP + 17 + dp.ROW_PITCH * row:
            line[30:1410] = b'\xff' * 1380
        rows.append(bytes(line))
    out = io.BytesIO()
    Image.frombytes('RGB', (480, 216), b''.join(rows)).save(out, 'PNG')
    return out.getvalue()


class FakeDevice:
    """A healthy XY Forever home: answers every command xyf_host.main() would send."""

    def __init__(self, fail=(), answer=True, hello=PRE_V1_HELLO, faults=()):
        self.fail, self.answer = set(fail), answer
        self.hello_text, self.faults, self.wire, self.opens = hello, set(faults), [], 0
        self.staging, self.completed, self.left = None, False, False  # [announced, received, data]
        self.calls, self.hold, self.crash, self.frames = [], False, 0, 0
        self.screen, self.dry, self.seq, self.default = 0, None, 7, 'opxy-121-stock'
        self.row = 0  # the selected home row (keys track1/track3/minus/plus)
        # B's write window: writes since B last closed it; B's STATUS fields; the menu catalog.
        self.writes, self.window = 0, dict(b_window=0, b_windows=0, b_catalogs=0)
        self.catalog = b''
        self.dirs = set()  # folders made with mkdir
        image = bytes(range(256)) * 40  # 10,240 B
        self.files = {
            BASE + 'state-a.txt': slot(self.default, self.seq),
            BASE + 'firmware/opxy-121-stock/info.txt': b'name=OP-XY 1.2.1\nbytes=%d\ncrc32=%08x\n'
            % (len(image), zlib.crc32(image)),
            BASE + 'firmware/opxy-121-stock/os.ldr': image,
        }

    def out(self, args, flag):
        return args[args.index(flag) + 1]

    def status(self):
        self.frames += 2
        usb = dict(
            version=4,
            role=1,
            frames=self.frames,
            crash_restarts=self.crash,
            retired_kicks=0,
            identity='0x0',
            put_size=0,
            reads=0,
        )
        if self.writes and 'no-settle' not in self.faults:  # B closed the window since the last call
            self.writes = 0
            self.window = dict(
                b_window=3, b_windows=self.window['b_windows'] + 1, b_catalogs=self.window['b_catalogs'] + 1
            )
            self.catalog = catalog_blob(self.files)
        return 'connected fake: XYF1\n' + json.dumps(
            dict(usb_service=usb, dry_run_file_boot=self.dry, remote_read=dict(self.window), blocks=['0x31425355:528']),
            indent=2,
        )

    def reboot(self):
        self.frames, self.crash, self.staging = 0, 0, None

    # -- protocol stage ports (dp.Wire -> FakeLink -> frame) ---------------------------------------
    def open(self, timeout):
        if not self.answer:
            raise dp.NoAnswer('XY Forever USB service not found')
        self.opens += 1
        return FakeLink(self), self.hello_text

    def frame(self, kind, payload):
        """One request as XY Forever's home_pump answers it (target/usb_service.cpp), with faults."""
        self.wire.append((kind, bytes(payload)))
        f, none = self.faults, bytes(8)
        if kind == P.HELLO:
            return 0, (self.hello_text + ('x' if 'hello-text' in f else '')).encode()
        if kind == P.STATUS:
            if self.left:
                raise TimeoutError('no reply to 0x02')
            return 0, b''
        if kind == P.READ_FILE:
            v = P.unpack(dp.READ_REQ, payload)
            if v['path'] not in self.files:
                return 8, b''
            data = self.files[v['path']]
            if 'offset' not in v:
                return (9, b'') if len(data) > 4096 else (0, data)
            data = data[v['offset'] : v['offset'] + v['length']]
            return 0, data[:100] if 'short-read' in f and v['offset'] >= 4096 else data
        if kind == P.PUT_BEGIN:
            self.staging = [P.unpack(dp.BEGIN_REQ, payload)['size'], 0, b'']
            return 0, b''
        if kind == P.PUT_DATA:
            if not self.staging or len(payload) < 5:
                return 3, b''
            at, data = struct.unpack_from('<I', payload)[0], payload[4:]
            if at != self.staging[1] or at + len(data) > self.staging[0]:
                return 4, b''
            if 'timeout-4k' in f and len(data) > 1024:
                raise TimeoutError('no reply to 0x11')
            self.staging[1] += len(data)
            self.staging[2] += data
            self.completed |= self.staging[1] == self.staging[0]
            return 0, struct.pack('<I', self.staging[1] + (1 if 'stage' in f and at else 0))
        if kind == P.PUT_END:
            if 'end' in f:
                return 0, b''
            complete = self.staging and self.staging[1] == self.staging[0]
            if not complete:
                self.staging = None
            return (0 if complete else 3), b''
        if kind == P.FILE_WRITE:
            path = payload.split(b'\0', 1)[0].decode()
            if len(path) + 1 + 32 != len(payload) and 'bad-request' not in f:
                return 1, none
            if not path.startswith(BASE) and 'refused' not in f:
                if 'leak' in f:
                    self.files[path] = b'leaked'
                return 14, none
            return 3, none  # never a complete staging here
        if kind == P.CONTINUE:
            assert payload == struct.pack('<I', 2), payload
            if 'link' in f:
                return 0, b''
            self.left = 'gone' in f
            return 12, b''
        return (0 if 'unsupported' in f else 2), b''

    def command(self, argv, timeout, **limits):
        key = ' '.join(a for a in argv if not a.startswith('/') and not a.startswith('--out'))
        self.calls.append(' '.join(argv))
        if not self.answer:
            return dp.NO_ANSWER, '', 'XY Forever USB service not found\n'
        for f in self.fail:
            if key.startswith(f):
                return 1, 'connected fake\n%s: failed\n' % argv[0], ''
        name = argv[0]
        if name == 'wait':  # HELLO only (#65: the check reads confirm=)
            return 0, 'connected fake: XYF1\n', ''
        if name == 'status':
            return 0, self.status(), ''
        if name == 'hold':
            self.hold = argv[1] == 'on'
            return 0, '%s %s: ok\n' % (name, argv[1]), ''
        if name == 'read':
            path, out = argv[1], Path(self.out(argv, '--out'))
            if path not in self.files:
                return 8, 'read failed: not_found\n', ''
            data = self.files[path]
            if '--offset' in argv:
                at = int(self.out(argv, '--offset'))
                data = data[at : at + 1024]
            out.write_bytes(data)
            return 0, 'connected fake\n', ''
        if name == 'screenshot':
            Path(self.out(argv, '--out')).write_bytes(screen_png(self.screen, self.row))
            return 0, 'screenshot: ok\n', ''
        if name == 'key':
            if argv[1] in ('track3', 'play'):
                self.screen += 1
            self.row = {
                'track1': 0,
                'track3': 2,
                'minus': (self.row - 1) % dp.HOME_ROWS,
                'plus': (self.row + 1) % dp.HOME_ROWS,
            }.get(argv[1], self.row)
            return 0, 'key %s (1): ok\n' % argv[1], ''
        if name == 'peek' and int(argv[1], 0) == dp.census.CATALOG_ADDRESS:
            Path(self.out(argv, '--out')).write_bytes(self.catalog.ljust(int(argv[2]), b'\0'))
            return 0, 'peek: catalog\n', ''
        if name == 'peek':
            reason = 2 | 0x400 | (0x100 if self.hold else 0)
            Path(self.out(argv, '--out')).write_bytes(
                struct.pack('<4I', 0x52465859, ~0x52465859 & 0xFFFFFFFF, reason, self.crash)
            )
            return 0, 'peek: 16 bytes\n', ''
        if name == 'reboot':
            self.reboot()
            return 0, 'reboot: ok\n', ''
        if name == 'continue':
            self.dry = dict(outcome='file', committed=2373336 + self.frames, first_answer_ms=117, longest_answer_ms=130)
            self.reboot()
            return 0, 'continue (dry run): ok\n', ''
        if name in ('put-file', 'put'):
            self.files[argv[2]] = Path(argv[1]).read_bytes()
            self.writes += 1
            return 0, '%s: ok\n' % name, ''
        if name == 'mods' and argv[1] == 'apply':  # into library entry mods-a
            out = '%sfirmware/mods-a' % BASE
            image = b'assembled' if 'mods-differ' not in self.faults else b'other'
            self.files.update(
                {
                    out + '/os.ldr': image,
                    out + '/info.txt': b'type=firmware\ngeneration=0.0.6\n',
                    out + '/content/display/fm2op.svg': b'<svg/>',
                }
            )
            self.dirs.update({out, out + '/content', out + '/content/display'})
            self.writes += 1
            self.catalog = catalog_blob(self.files)
            return (
                0,
                (
                    'mods apply: job 1 started; output %stmp/job-1/\njob 1: done 7/7, 45.0 s, 8496 steps\n'
                    'mods apply: done; library entry mods-a (%s/)\n' % (BASE, out)
                ),
                '',
            )
        if name == 'boot':  # content not installed: refused
            return (0, 'boot: ok\n', '') if 'boot-ok' in self.faults else (17, 'boot %s: no_content\n' % argv[1], '')
        if name == 'ls' and 'empty-ls' in self.faults:  # build 165 on the device
            return 0, 'connected fake\n[]', ''
        if name == 'ls':  # files and the folders they imply
            path = argv[1].rstrip('/') + '/'
            rows = {}
            for f, data in self.files.items():
                if f.startswith(path):
                    leaf = f[len(path) :].split('/')
                    rows[leaf[0]] = dict(
                        name=leaf[0], type='file' if len(leaf) == 1 else 'dir', size=len(data) if len(leaf) == 1 else 0
                    )
            rows.update(
                {
                    d[len(path) :]: dict(name=d[len(path) :], type='dir', size=0)
                    for d in self.dirs
                    if d.startswith(path) and '/' not in d[len(path) :]
                }
            )
            return 0, 'connected fake\n' + json.dumps(list(rows.values())), ''
        if name == 'get':
            Path(argv[2]).write_bytes(self.files[argv[1]])
            return 0, 'get: ok\n', ''
        if name == 'mkdir':
            self.dirs.add(argv[1])
            return 0, 'mkdir: ok\n', ''
        if name == 'rm':
            path = argv[-1]
            if '--dir' in argv:
                if any(f.startswith(path + '/') for f in self.files):
                    return 3, 'rm: state\n', ''
                found = path in self.dirs
                self.dirs.discard(path)
            else:
                found = self.files.pop(path, None) is not None
            self.writes += 1
            return (0, 'rm: ok\n', '') if found else (8, 'rm: not_found\n', '')
        if name == 'mv':  # a file or a folder, never onto a name
            source, target = argv[1], argv[2]

            def held(p):
                return (
                    p in self.files
                    or p in self.dirs
                    or any(k.startswith(p + '/') for k in list(self.files) + list(self.dirs))
                )

            if held(target):
                return 3, 'mv: state (error 17)\n', ''
            if not held(source):
                return 8, 'mv: not_found (error 2)\n', ''
            self.files = {
                (target + k[len(source) :] if k == source or k.startswith(source + '/') else k): v
                for k, v in self.files.items()
            }
            self.dirs = {
                target + d[len(source) :] if d == source or d.startswith(source + '/') else d for d in self.dirs
            }
            self.writes += 1
            return 0, 'mv: ok\n', ''
        if name == 'cp':  # a file, never onto a name
            source, target = argv[1], argv[2]
            if target in self.files or target in self.dirs:
                return 3, 'cp: state (error 17)\n', ''
            if source not in self.files:
                return 8, 'cp: not_found\n', ''
            self.files[target] = self.files[source]
            self.writes += 1
            return 0, 'cp: ok\n', ''
        if name == 'set-default':
            self.default, self.seq = argv[1], self.seq + 1
            self.files[BASE + ('state-b.txt' if self.seq % 2 else 'state-a.txt')] = slot(self.default, self.seq)
            return 0, 'verified: default=%s\n' % argv[1], ''
        if name == 'install':
            return 0, 'install: ok, written 79312, active partition 1, verified sectors 155\n', ''
        if name == 'mtp-test':
            self.frames += 1
            return (
                0,
                'connected fake\n'
                + json.dumps(dict(status='ok', step=11, mounted=1, unmounted=1, entries=9, read=36, wait_ms=2000)),
                '',
            )
        return 2, 'unknown %s\n' % name, ''

    def port_gone(self, timeout):
        return True


class FakeLink:
    """host.Device as dp.HostLink uses it: call() refuses types missing from cmds, raw() does not."""

    def __init__(self, dev):
        self.dev, self.hello = dev, P.Hello(dev.hello_text)

    def call(self, kind, payload, timeout):
        if not self.hello.answers(kind):
            raise dp.host.Unsupported('not in cmds')
        return self.dev.frame(kind, payload)

    def raw(self, kind, payload, timeout):
        return self.dev.frame(kind, payload)

    def close(self):
        pass


class FakeKeepalive:
    def __init__(self):
        self.started, self.stops = [], 0

    def stop_running(self):
        self.stops += 1
        return []

    def start(self, folder):
        self.started.append(folder)
        (folder / 'keepalive.json').write_text('{"usb_service": {}}')
        return 4242


class ModsRowTest(unittest.TestCase):
    """The Mods page row of a package: kind, then name (A-Z folded), then folder (packages only)."""

    def test_sorted_order(self):
        sizes = 'order=0\nobjects=1\ncode=1\ndata=1\nstate=1\n'
        with tempfile.TemporaryDirectory() as tmp:
            for folder, kind, name in (
                ('base', 'synth', 'Base'),
                ('fm2op', 'synth', 'fm2op'),
                ('acid', 'synth', 'Zed'),
                ('kick', 'drum', 'Kick'),
                ('ext-tool', 'extension', 'A tool'),
                ('beta', 'synth', 'FM2OP'),
                ('bad', 'robot', 'Bad'),
                ('ext-x', 'synth', 'X'),
            ):
                (Path(tmp) / folder).mkdir()
                (Path(tmp) / folder / 'info.txt').write_text('name=%s\nmachine=%s\n%s' % (name, kind, sizes))
                if folder != 'kick':
                    (Path(tmp) / folder / 'files.sha256').write_text('')
            # synths: FM2OP (beta), fm2op (fm2op), Zed (acid); extensions: A tool (kick: no files.sha256)
            self.assertEqual(dp.mods_row(tmp, 'beta'), (0, 4))
            self.assertEqual(dp.mods_row(tmp, 'fm2op'), (1, 4))
            self.assertEqual(dp.mods_row(tmp, 'acid'), (2, 4))
            self.assertIsNone(dp.mods_row(tmp, 'kick'))
            self.assertEqual(dp.mods_row(tmp, 'ext-tool'), (3, 4))
            self.assertIsNone(dp.mods_row(tmp, 'bad'))
            self.assertIsNone(dp.mods_row(tmp, 'ext-x'))
            self.assertIsNone(dp.mods_row(tmp, 'missing'))

    def test_screen_row(self):
        self.assertEqual([dp.mods_screen_row(p, 11) for p in range(11)], [0, 1, 2, 3, 4, 4, 4, 5, 6, 7, 8])  # 9 rows
        self.assertEqual(dp.mods_screen_row(4, 6), 4)
        self.assertEqual(dp.mods_screen_row(40, 81), 4)
        self.assertEqual(dp.mods_screen_row(80, 81), 8)


class DevicePassTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.sessions = Path(temp.name).resolve() / 'private/xy-forever/sessions'
        self.sessions.mkdir(parents=True)
        p = patch.object(dp.paths, 'PRIVATE_ROOT', self.sessions.parents[1])
        p.start()
        self.addCleanup(p.stop)

    def run_pass(self, argv=(), dev=None):
        self.dev, self.keepalive = dev or FakeDevice(), FakeKeepalive()
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = dp.main(
                list(argv), device=self.dev, keepalive=self.keepalive, sessions=self.sessions, sleep=lambda s: None
            )
        self.out = out.getvalue()
        folders = sorted(self.sessions.iterdir(), key=lambda p: int(p.name.rsplit('-', 1)[1]))  # 10 after 9
        self.folder = folders[-1] if folders else None
        self.summary = json.loads((self.folder / 'summary.json').read_text()) if self.folder else None
        return code

    def rows(self):
        return {(r['stage'], r['step']): r for r in self.summary['steps']}

    def test_configuration_guard_uses_the_selected_record(self):
        from scripts.device_pass_library import read_config
        from xy_forever import native_json

        rig = dp.Pass(FakeDevice(), None, None)
        names = ['config.json']
        native = native_json.config_document(dict(base='opxy-121-stock', mods='alpha', safe='1'))
        with patch.object(rig, 'names', return_value=names), patch.object(rig, 'read', return_value=native) as read:
            selected = read_config(rig, dp.StepSkip)
            self.assertEqual((selected['mods'], selected['safe']), ('alpha', '1'))
            self.assertTrue(read.call_args.args[0].endswith('/config.json'))
            names.append('config.txt')
            read.reset_mock(return_value=True)
            read.return_value = native.replace(b'"schema_version":1', b'"schema_version":2')
            with self.assertRaisesRegex(dp.StepSkip, 'config.json: unsupported JSON schema version'):
                read_config(rig, dp.StepSkip)
            read.assert_called_once_with(dp.config_records.ROOT + '/config.json', 'config-before-config.json', '--all')

    def test_saved_state_compares_all_four_records_as_bytes(self):
        rig = dp.Pass(FakeDevice(), None, None)
        raw = {'config.json': b'{}\n', 'config.txt': b'format=1\n', 'config.old': b'# backup\n'}

        def read(path, *_):
            return raw.get(path.rsplit('/', 1)[1])

        with (
            patch.object(rig, 'read', side_effect=read),
            patch.object(rig, 'names', return_value=['opxy-121']),
            patch.object(rig, 'slots', return_value=(None, None, {'default': 'opxy-121'})),
            patch.object(rig, 'home_view', return_value={'band': b'home'}),
        ):
            before = rig.saved_state('before')
            self.assertEqual(
                {name: before[name] for name in dp.config_records.NAMES}, dict(raw, **{'config.json.old': None})
            )
            self.assertIn('as found', rig.same_state(before, 'same'))
            raw['config.json.old'] = b'{}\n'
            with self.assertRaisesRegex(dp.StepFail, 'config.json.old'):
                rig.same_state(before, 'changed')

    def test_simulator_run_keeps_its_folder_only_on_failure_or_keep(self):
        """--sim: the evidence folder is removed when nothing failed, unless --keep; a FAIL keeps it.
        (A real-device run always keeps it: every other test here.)"""
        sim = ['--sim', '127.0.0.1:7341']
        try:
            self.assertEqual(self.run_pass(sim), dp.EXIT_PASS)
            self.assertIsNone(self.folder)
            self.assertRegex(self.out.splitlines()[-1], r'^folder: .*-sim-pass-1 removed \(simulator run')
            self.assertEqual(self.run_pass(sim + ['--keep']), dp.EXIT_PASS)
            self.assertTrue(self.folder.name.endswith('-sim-pass-1'))
            self.assertEqual(self.run_pass(sim, dev=FakeDevice(fail={'continue --dry-run'})), dp.EXIT_FAIL)
            self.assertTrue(self.folder.name.endswith('-sim-pass-2'))
            self.assertEqual(self.summary['failed'], 1)
        finally:
            dp.host.SIM = None

    def test_ask_before_usb_control_stops_the_pass(self):
        """#65: a HELLO with confirm=1 (the device would ask before any other command) FAILs the check with what to
        switch off, before STATUS; every later step is SKIP."""
        self.assertTrue(dp.confirm_on('connected 127.0.0.1:7341: XYF1;proto=1.23;confirm=1;stage2=0\n{}'))
        self.assertFalse(dp.confirm_on('connected fake: XYF1;confirm=0;stage2=0\n'))
        self.assertFalse(dp.confirm_on('connected fake: XYF1\n'))  # a build before 1.23: no gate

        class Asking(FakeDevice):
            def command(self, argv, timeout, **limits):
                code, out, err = super().command(argv, timeout, **limits)
                return code, out.replace('connected fake: XYF1\n', 'connected fake: XYF1;confirm=1\n'), err

        asking = Asking()
        self.assertEqual(self.run_pass(dev=asking), dp.EXIT_FAIL)
        check = self.rows()[('check', 'check')]
        self.assertEqual((check['result'], check['detail']), ('FAIL', dp.CONFIRM_ON))
        self.assertEqual(dp.CONFIRM_ON, 'Switch off Settings > Ask before USB control on the device first')
        self.assertEqual(asking.calls, ['wait'])  # nothing that would ask, the end stage either
        later = [r for r in self.summary['steps'] if r['stage'] not in ('check', 'end')]
        self.assertTrue(later and all(r['result'] == 'SKIP' for r in later))

    def test_no_answer_prints_one_need_line_and_changes_nothing(self):
        code = self.run_pass(dev=FakeDevice(answer=False))
        self.assertEqual(code, dp.EXIT_NEED)
        self.assertNotEqual(dp.EXIT_NEED, dp.EXIT_FAIL)
        lines = self.out.splitlines()
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith('NEED: '))
        self.assertEqual(self.dev.calls, ['wait'])
        self.assertIsNone(self.folder)
        self.assertEqual(self.keepalive.started, [])

    def test_default_stages_in_order_all_pass(self):
        self.assertEqual(self.run_pass(dev=with_121(FakeDevice())), dp.EXIT_PASS)
        order = [r['stage'] for r in self.summary['steps']]
        stages = [s for s, _ in dp.STAGES]
        self.assertEqual(sorted(set(order), key=order.index), stages)
        self.assertEqual(order, sorted(order, key=stages.index))
        ran = {r['stage'] for r in self.summary['steps'] if r['result'] == 'PASS'}
        self.assertEqual(ran, {'check', 'session', 'read', 'boot', 'writes', 'mtp', 'end'})
        self.assertEqual(self.rows()[('config', 'pending')]['result'], 'SKIP')  # no package set
        found = dp.mods_row(dp.MODS_PACKAGES, 'fm2op')  # (position, rows)
        self.assertTrue(found is None or 0 <= found[0] < found[1])
        for stage in ('install',):
            for r in self.summary['steps']:
                if r['stage'] == stage:
                    self.assertEqual((r['result'], r['detail']), ('SKIP', 'not selected'))
        self.assertEqual(self.summary['failed'], 0)
        # The set-default round trip ends on the starting default; no step booted the OS.
        self.assertEqual(self.dev.default, 'opxy-121-stock')
        self.assertIn('set-default opxy-121 --yes-write', self.dev.calls)
        self.assertFalse(any(c.startswith('continue') and '--dry-run' not in c for c in self.dev.calls))
        self.assertFalse(any('--to os' in c for c in self.dev.calls))
        self.assertFalse(any(c.startswith(('guard', 'test-hang')) for c in self.dev.calls))  # no guard step anywhere
        self.assertFalse(self.dev.hold)
        self.assertEqual(self.keepalive.started, [self.folder])

    def build(self, image=IMAGE, name='build', version='0.1.0'):
        build = self.sessions.parent / name
        build.mkdir(exist_ok=True)
        (build / 'manifest.json').write_text(
            json.dumps(
                dict(schema=2, version=version, image=image, mode='xyf-product', candidate_sha256=image + '0' * 56)
            )
        )
        return build

    def test_install_stage_runs_in_place(self):
        build = self.build()
        with patch.object(dp.host, 'load_build', return_value=(b'image', ['b', 'LDR ok'])):
            code = self.run_pass(['--install', str(build)], dev=FakeDevice(hello=V1_HELLO))
        self.assertEqual(code, dp.EXIT_PASS, self.out)
        passed = [r['stage'] for r in self.summary['steps'] if r['result'] == 'PASS']
        self.assertEqual(
            sorted(set(passed), key=passed.index),
            ['check', 'session', 'read', 'install', 'protocol', 'boot', 'writes', 'mtp', 'end'],
        )
        install = self.dev.calls.index('install %s --yes-install' % build)
        self.assertLess(next(i for i, c in enumerate(self.dev.calls) if c.startswith('peek')), install)
        self.assertLess(install, next(i for i, c in enumerate(self.dev.calls) if c.startswith('continue')))
        self.assertFalse(any(c.startswith('restart-a') for c in self.dev.calls))
        # The pass ends with hold off; no guard step anywhere (there is no guard).
        self.assertEqual(
            [r['step'] for r in self.summary['steps'] if r['stage'] == 'end'], ['end-status', 'hold-off', 'keepalive']
        )
        self.assertIn('hold off', self.dev.calls[self.dev.calls.index('mtp-test --hold-ms 2000 --yes-test') :])
        self.assertFalse(any(c.startswith(('guard', 'test-hang')) for c in self.dev.calls))
        self.assertFalse(self.dev.hold)
        self.assertEqual(self.dev.crash, 0)
        self.assertNotIn('guard', {r['stage'] for r in self.summary['steps']})

    def test_the_guard_test_option_is_gone(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            dp.parse_args(['--guard-test'])

    def test_a_restart_after_mtp_fails_home_after_mtp_and_still_ends_the_session(self):
        build = self.build()
        dev = FakeDevice(hello=V1_HELLO)
        real = dev.command

        def restarted(argv, timeout, **limits):
            result = real(argv, timeout, **limits)
            if argv[0] == 'mtp-test':
                dev.crash += 1  # a lost link restarted both
            return result

        dev.command = restarted
        with patch.object(dp.host, 'load_build', return_value=(b'image', ['b', 'LDR ok'])):
            code = self.run_pass(['--install', str(build)], dev=dev)
        self.assertEqual(code, dp.EXIT_FAIL)
        rows = self.rows()
        self.assertEqual(rows[('mtp', 'home-after-mtp')]['result'], 'FAIL')
        self.assertIn('crash restarts 0 -> 1', rows[('mtp', 'home-after-mtp')]['detail'])
        for name in ('end-status', 'hold-off', 'keepalive'):
            self.assertEqual(rows[('end', name)]['result'], 'PASS')

    def test_stop_on_failure_skips_the_rest_and_still_ends_the_session(self):
        code = self.run_pass(dev=FakeDevice(fail={'continue --dry-run'}))
        self.assertEqual(code, dp.EXIT_FAIL)
        rows = self.rows()
        self.assertEqual(rows[('boot', 'dry-run')]['result'], 'FAIL')
        self.assertIn('continue', rows[('boot', 'dry-run')]['detail'])
        for key in (('boot', 'restart'), ('boot', 'keys'), ('writes', 'put-file'), ('mtp', 'mtp-test')):
            self.assertEqual(rows[key]['result'], 'SKIP')
            self.assertEqual(rows[key]['detail'], 'after FAIL of boot/dry-run')
        failed_at = self.dev.calls.index('continue --dry-run')
        later = self.dev.calls[failed_at + 1 :]
        self.assertIn('hold off', later)
        self.assertFalse(any(c.startswith(('key', 'put-file', 'mtp-test')) for c in later))
        for name in ('end-status', 'hold-off', 'keepalive'):
            self.assertEqual(rows[('end', name)]['result'], 'PASS')
        self.assertEqual(self.keepalive.started, [self.folder])

    def test_failure_inside_a_stage_stops_that_stage(self):
        self.assertEqual(self.run_pass(dev=FakeDevice(fail={'key track3'})), dp.EXIT_FAIL)
        rows = self.rows()
        self.assertEqual(rows[('boot', 'keys')]['result'], 'FAIL')
        self.assertNotIn('key play', self.dev.calls)  # PLAY is never sent blind

    def test_content_switch_skips(self):
        """content/switch is SKIP with the reason on a device without the package set (the simulator pass
        runs it for real: scripts/device_pass.py --sim)."""
        self.assertEqual(self.run_pass(['--stage', 'content']), dp.EXIT_PASS)
        row = self.rows()[('content', 'switch')]
        self.assertEqual(row['result'], 'SKIP', row)
        self.assertIn('package set', row['detail'])
        self.assertFalse([c for c in self.dev.calls if c.startswith(('set-default', 'mods'))])

    def test_mods_apply_step(self):
        """SKIP without a package set on the device (the default fake), or without a base entry holding
        content.sha256; else mods apply --wait, get os.ldr, equal to the host reference, the output removed
        (left when it differs)."""
        self.assertEqual(self.run_pass(['--stage', 'mods']), dp.EXIT_PASS)
        self.assertEqual(self.rows()[('mods', 'apply')]['result'], 'SKIP')
        gen = BASE + 'mods/%s/' % dp.MODS_PACKAGES.name

        def holding(base_list=True, faults=()):
            dev = FakeDevice(faults=faults)
            dev.files.update({gen + 'base/info.txt': b'x', gen + 'fm2op/info.txt': b'y'})
            if base_list:
                dev.files[BASE + 'firmware/opxy-121-stock/content.sha256'] = b'z'
            return dev

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(dp, 'MODS_PACKAGES', Path(tmp)),
            patch.object(dp, 'MODS_CATALOG', Path(tmp) / 'base/info.txt'),
            patch.object(dp, 'mods_reference', lambda *a: hashlib.sha256(b'assembled').hexdigest()),
        ):
            (Path(tmp) / 'base').mkdir()
            (Path(tmp) / 'base/info.txt').write_text('x')
            gen = BASE + 'mods/%s/' % Path(tmp).name
            self.run_pass(['--stage', 'mods'], dev=holding(base_list=False))
            self.assertEqual(self.rows()[('mods', 'apply')]['result'], 'SKIP')
            self.assertIn('content.sha256', self.rows()[('mods', 'apply')]['detail'])
            self.assertEqual(self.run_pass(['--stage', 'mods'], dev=holding()), dp.EXIT_PASS)
            row = self.rows()[('mods', 'apply')]
            self.assertEqual(row['result'], 'PASS', row)
            self.assertIn('identical to the host reference', row['detail'])
            self.assertIn('base opxy-121-stock', row['detail'])
            self.assertIn('library entry mods-a', row['detail'])
            self.assertIn('boot refused (no_content)', row['detail'])
            self.assertFalse(
                [f for f in self.dev.files if '/mods-a' in f] + [d for d in self.dev.dirs if '/mods-a' in d]
            )
            self.assertIn('boot mods-a --yes-boot', self.dev.calls)
            self.run_pass(['--stage', 'mods'], dev=holding(faults={'boot-ok'}))
            row = self.rows()[('mods', 'apply')]
            self.assertEqual(row['result'], 'FAIL')
            self.assertIn('expected no_content', row['detail'])
            dev = holding()
            dev.files.update({BASE + 'firmware/mods-a/os.ldr': b'a', BASE + 'firmware/mods-b/os.ldr': b'b'})
            self.run_pass(['--stage', 'mods'], dev=dev)
            self.assertEqual(self.rows()[('mods', 'apply')]['result'], 'SKIP')
            self.assertFalse([c for c in dev.calls if c.startswith('mods apply')])
            self.run_pass(['--stage', 'mods'], dev=holding(faults={'mods-differ'}))
            row = self.rows()[('mods', 'apply')]
            self.assertEqual(row['result'], 'FAIL')
            self.assertIn('differs from the host reference', row['detail'])
            self.assertIn(BASE + 'firmware/mods-a/os.ldr', self.dev.files)

    def test_write_window_steps(self):
        """Two writes then one window closed by B; the scratch entry's name in the menu catalog;
        a window that never closes is a FAIL that says so."""
        self.assertEqual(self.run_pass(['--stage', 'writes']), dp.EXIT_PASS)
        rows = self.rows()
        self.assertIn('one window for both writes', rows[('writes', 'window-closed')]['detail'])
        self.assertIn(dp.SCRATCH_ID, rows[('writes', 'library-change')]['detail'])
        puts = [c.split()[2] for c in self.dev.calls if c.startswith('put-file')]
        self.assertEqual(puts, [dp.PASS_FILE, dp.PASS_FILE2, dp.SCRATCH_INFO])
        self.assertTrue(all(p.startswith(BASE + 'log/') or p == dp.SCRATCH_INFO for p in puts))
        # a file into a subfolder, the subfolder as a whole, onto an existing name (state); all removed.
        self.assertEqual(rows[('writes', 'rename')]['result'], 'PASS')
        self.assertIn('mv sub sub2 (a folder with a file)', rows[('writes', 'rename')]['detail'])
        self.assertEqual(
            [c for c in self.dev.calls if c.startswith('mv ')],
            [
                'mv %s/%s %s/%s' % (dp.FILES_DIR, a, dp.FILES_DIR, b)
                for a, b in (('a.txt', 'sub/b.txt'), ('sub', 'sub2'), ('sub2/b.txt', 'sub2'))
            ],
        )
        self.assertFalse(
            any(k.startswith(dp.FILES_DIR) for k in self.dev.files)
            or any(d.startswith(dp.FILES_DIR) for d in self.dev.dirs)
        )
        # a file copied on the device, both read back, onto an existing name (state); all removed.
        self.assertEqual(rows[('writes', 'copy')]['result'], 'PASS')
        self.assertIn('cp 100000 B on the device', rows[('writes', 'copy')]['detail'])
        self.assertEqual(
            [c for c in self.dev.calls if c.startswith('cp ')],
            ['cp %s/a.bin %s/b.bin' % (dp.FILES_DIR, dp.FILES_DIR)] * 2,
        )
        self.run_pass(['--stage', 'writes'], dev=FakeDevice(faults={'no-settle'}))
        row = self.rows()[('writes', 'window-closed')]
        self.assertEqual(row['result'], 'FAIL')
        self.assertIn('the write window may still be open', row['detail'])

    def test_an_empty_listing_never_passes(self):
        """Build 165 listed every folder empty with status ok; the read stage's ls-library,
        scratch-cleanup and file-commands fail on it instead of passing vacuously."""
        self.run_pass(['--stage', 'read'], dev=FakeDevice(faults={'empty-ls'}))
        row = self.rows()[('read', 'ls-library')]
        self.assertEqual(row['result'], 'FAIL')
        self.assertIn('no opxy-121-stock', row['detail'])
        self.run_pass(['--stage', 'writes'], dev=FakeDevice(faults={'empty-ls'}))
        row = self.rows()[('writes', 'scratch-cleanup')]
        self.assertEqual(row['result'], 'FAIL')
        self.assertIn('the listing is wrong', row['detail'])
        # file-commands alone (scratch-cleanup failing stops the stage): its own check.
        rig = dp.Pass(FakeDevice(faults={'empty-ls'}), None, None)
        with self.assertRaisesRegex(dp.StepFail, 'lists no firmware'):
            rig.step_file_commands()
        self.assertEqual(self.run_pass(['--stage', 'read,writes']), dp.EXIT_PASS)
        self.assertIn('firmware/ lists 1 entries with opxy-121-stock', self.rows()[('read', 'ls-library')]['detail'])

    def test_set_default_alt_only_to_a_listed_entry_with_its_content(self):
        """The device library: opxy-121-stock (the default) and mods-a, content not installed. No opxy-121
        listed: no set-default at all (it would fail, or start a content switch on mods-a); both steps SKIP."""
        dev = FakeDevice()
        dev.files.update(
            {BASE + 'firmware/mods-a/info.txt': b'name=Mods\ngeneration=1\n', BASE + 'firmware/mods-a/os.ldr': b'a'}
        )
        self.assertEqual(self.run_pass(['--stage', 'writes'], dev=dev), dp.EXIT_PASS)
        rows = self.rows()
        alt, back = rows[('writes', 'set-default-alt')], rows[('writes', 'set-default-back')]
        self.assertEqual((alt['result'], back['result']), ('SKIP', 'SKIP'), (alt, back))
        self.assertIn(
            'the menu lists: mods-a (content not installed), opxy-121-stock, xyf-pass-scratch (unusable)', alt['detail']
        )
        self.assertFalse([c for c in dev.calls if c.startswith('set-default')])
        # Listed and installed: chosen (ALT_DEFAULTS first), and back.
        self.assertEqual(self.run_pass(['--stage', 'writes'], dev=with_121(dev)), dp.EXIT_PASS)
        self.assertIn('opxy-121-stock -> opxy-121', self.rows()[('writes', 'set-default-alt')]['detail'])
        self.assertEqual(dev.default, 'opxy-121-stock')

    def test_file_commands_remove_a_leftover_folder_without_listing_it(self):
        dev = FakeDevice(faults={'empty-ls'})
        for leaf in dp.FILES_LEAVES:
            dev.files[dp.FILES_DIR + '/' + leaf] = b'old'
        dev.dirs.add(dp.FILES_DIR)
        with self.assertRaisesRegex(dp.StepFail, 'lists no firmware'):
            dp.Pass(dev, None, None).step_file_commands()
        removed = [c for c in dev.calls if c.startswith('rm ')]
        self.assertEqual(
            removed, ['rm %s/big.bin' % dp.FILES_DIR, 'rm %s/small.txt' % dp.FILES_DIR, 'rm --dir %s' % dp.FILES_DIR]
        )
        self.assertFalse(any(k.startswith(dp.FILES_DIR) for k in dev.files) or dp.FILES_DIR in dev.dirs)
        dev = FakeDevice()  # and a working listing then passes
        for leaf in dp.FILES_LEAVES:
            dev.files[dp.FILES_DIR + '/' + leaf] = b'old'
        dev.dirs.add(dp.FILES_DIR)
        self.assertEqual(self.run_pass(['--stage', 'writes'], dev=dev), dp.EXIT_PASS)
        self.assertIn('leftover removed', self.rows()[('writes', 'file-commands')]['detail'])

    def test_end_steps_run_when_the_device_is_lost(self):
        dev = FakeDevice()
        real = dev.command

        def vanish(argv, timeout, **limits):
            if argv[0] == 'mtp-test':
                dev.answer = False
            return real(argv, timeout, **limits)

        dev.command = vanish
        self.assertEqual(self.run_pass(dev=dev), dp.EXIT_FAIL)
        rows = self.rows()
        self.assertEqual(rows[('mtp', 'mtp-test')]['result'], 'FAIL')
        self.assertEqual(rows[('end', 'end-status')]['result'], 'FAIL')
        for name in ('hold-off', 'keepalive'):
            self.assertEqual(rows[('end', name)]['result'], 'SKIP')
            self.assertIn('power on holding PLAY', rows[('end', name)]['detail'])
        self.assertEqual(self.keepalive.started, [])

    def test_table_and_summary(self):
        self.run_pass(dev=with_121(FakeDevice(fail={'set-default opxy-121-stock'})))
        lines = self.out.splitlines()
        self.assertEqual(lines[0].split()[:4], ['step', 'result', 'seconds', 'detail'])
        self.assertEqual(lines[-1], 'folder: %s' % self.folder)
        self.assertEqual(len(lines), len(self.summary['steps']) + 2)
        self.assertTrue(self.folder.name.endswith('-device-pass-1'))
        for row, line in zip(self.summary['steps'], lines[1:-1]):
            self.assertEqual(line.split()[:2], ['%s/%s' % (row['stage'], row['step']), row['result']])
            self.assertEqual(set(row), {'stage', 'step', 'result', 'seconds', 'detail', 'file'})
            if row['result'] != 'SKIP':
                self.assertTrue((self.folder / row['file']).is_file())
        back = self.rows()[('writes', 'set-default-back')]
        self.assertEqual(back['result'], 'FAIL')
        self.assertIn('set-default opxy-121-stock --yes-write', back['detail'])
        log = (self.folder / back['file']).read_text()
        self.assertIn('$ xyf_host.py --timeout 5 set-default opxy-121-stock --yes-write', log)
        self.assertEqual((self.summary['exit_code'], self.summary['failed']), (dp.EXIT_FAIL, 1))
        self.assertEqual(self.summary['default_at_start'], 'opxy-121-stock')
        self.assertEqual(self.summary['keepalive_pid'], 4242)
        # A second pass on the same day gets the next folder.
        self.run_pass()
        self.assertTrue(self.folder.name.endswith('-device-pass-2'))

    def test_plan_touches_nothing(self):
        class Untouchable:
            def __getattr__(self, name):
                raise AssertionError('--plan used %s' % name)

        out = io.StringIO()
        with (
            contextlib.redirect_stdout(out),
            patch.object(dp, 'HostDevice', Untouchable),
            patch.object(dp, 'Keepalive', Untouchable),
        ):
            code = dp.main(['--plan'], device=Untouchable(), keepalive=Untouchable(), sessions=self.sessions)
        self.assertEqual(code, dp.EXIT_PASS)
        self.assertEqual(list(self.sessions.iterdir()), [])
        text = out.getvalue()
        for stage, steps in dp.STAGES:
            self.assertIn('%s (%s)' % (stage, 'off' if stage == 'install' else 'on'), text)
            for name, _, _ in steps:
                self.assertIn(name, text)

    def test_never_boots_the_os(self):
        for argv in (
            ['continue'],
            ['continue', '--menu'],
            ['reboot', '--yes-reboot', '--to', 'os'],
            ['uninstall', 'x', '--yes-uninstall'],
        ):
            with self.assertRaises(ValueError):
                dp.refuse_os(argv)
        dp.refuse_os(['continue', '--dry-run'])
        dp.refuse_os(['reboot', '--yes-reboot', '--to', 'menu'])

    def test_bad_stage_or_build_is_a_usage_error(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(
                dp.main(
                    ['--stages', 'read,flash'], device=FakeDevice(), keepalive=FakeKeepalive(), sessions=self.sessions
                ),
                dp.EXIT_USAGE,
            )
            self.assertEqual(
                dp.main(
                    ['--install', str(self.sessions / 'missing')],
                    device=FakeDevice(),
                    keepalive=FakeKeepalive(),
                    sessions=self.sessions,
                ),
                dp.EXIT_USAGE,
            )
        self.assertEqual(list(self.sessions.iterdir()), [])

    @pytest.mark.gate
    def test_host_device_closes_ports_and_reports_no_answer(self):
        dev = dp.HostDevice()
        with patch.object(dp.host, 'find_port', return_value=None):
            code, out, err = dev.command(['status'], 0.2)
            with self.assertRaises(dp.NoAnswer):
                dev.open(0.2)
        self.assertEqual(code, dp.NO_ANSWER)
        self.assertIn('not found', err)

    # -- protocol stage ----------------------------------------------------------------------------
    def protocol_rows(self):
        return [r for r in self.summary['steps'] if r['stage'] == 'protocol']

    def test_pre_v1_device_skips_every_protocol_step(self):
        self.assertEqual(self.run_pass(), dp.EXIT_PASS)
        rows = self.protocol_rows()
        self.assertEqual([r['step'] for r in rows], PROTOCOL_STEPS)
        self.assertEqual({r['result'] for r in rows}, {'SKIP'})
        self.assertEqual(rows[0]['detail'], 'pre-v1 device; HELLO %s' % PRE_V1_HELLO)
        self.assertEqual({r['detail'] for r in rows[1:]}, {'pre-v1 device'})
        self.assertEqual((self.dev.opens, self.dev.wire), (1, []))  # nothing sent but the connect HELLO
        self.assertEqual(self.summary['protocol'], dict(hello=PRE_V1_HELLO, pre_v1=True))
        self.assertEqual(self.summary['failed'], 0)

    def test_v1_device_passes_every_protocol_check(self):
        self.assertEqual(self.run_pass(dev=FakeDevice(hello=V1_HELLO)), dp.EXIT_PASS, self.out)
        rows = {r['step']: r for r in self.protocol_rows()}
        self.assertEqual({r['result'] for r in rows.values()}, {'PASS'}, rows)
        self.assertIn(
            'protocol %s, version 0.1.0, image %s (product)' % (P.ids.VERSION, IMAGE), rows['hello']['detail']
        )
        self.assertIn(V1_HELLO, rows['hello']['detail'])
        self.assertIn('recorded (no --install to compare)', rows['build']['detail'])
        self.assertIn('type 0x5f', rows['unsupported']['detail'])
        self.assertIn('refused; absent before and after', rows['refused']['detail'])
        self.assertIn('bad_request', rows['bad-request']['detail'])
        self.assertIn('link; STATUS still answers', rows['continue-link']['detail'])
        self.assertIn('1024 B ranges: 10 KiB in', rows['read-speed']['detail'])
        self.assertIn('read=1024: no larger range', rows['read-speed']['detail'])
        self.assertIn('1024 B chunks: 64 KiB in', rows['stage-speed']['detail'])
        proto = self.summary['protocol']
        self.assertEqual(
            (proto['hello'], proto['image'], proto['version'], proto['read'], proto['hello_ms']['n']),
            (V1_HELLO, IMAGE, '0.1.0', '1024', dp.HELLO_ROUNDS),
        )
        self.assertEqual((set(proto['read_kib_s']), set(proto['stage_kib_s'])), ({'1024'}, {'1024'}))
        # Placed after read (and install), before the boot stage restarts the device.
        order = [r['stage'] for r in self.summary['steps']]
        self.assertLess(order.index('read'), order.index('protocol'))
        self.assertLess(order.index('install'), order.index('protocol'))
        self.assertLess(order.index('protocol'), order.index('boot'))

    def test_protocol_stage_sends_nothing_that_writes(self):
        dev = FakeDevice(hello=V1_HELLO)
        files = dict(dev.files)
        self.assertEqual(self.run_pass(['--stages', 'protocol'], dev=dev), dp.EXIT_PASS, self.out)
        self.assertEqual(dev.files, files)
        self.assertFalse(dev.completed)  # no staging was ever complete ...
        self.assertIsNone(dev.staging)  # ... and stage.end discarded each one
        kinds = {kind for kind, _ in dev.wire}
        self.assertLessEqual(
            kinds, {P.HELLO, P.STATUS, P.READ_FILE, P.PUT_BEGIN, P.PUT_DATA, P.PUT_END, P.FILE_WRITE, P.CONTINUE, 0x5F}
        )
        for kind, payload in dev.wire:
            if kind == P.FILE_WRITE:
                self.assertFalse(payload.startswith(BASE.encode()))
            if kind == P.CONTINUE:
                self.assertEqual(payload, struct.pack('<I', 2))
            if kind == P.PUT_DATA:  # no frame above 1 KiB without --big-replies
                self.assertLessEqual(len(payload), 4 + 1024)
            if kind == P.READ_FILE:  # ranged reads only: no reply above read=
                self.assertIn('length', P.unpack(dp.READ_REQ, payload))
        self.assertFalse(
            any(c.startswith(('put-file', 'set-default', 'install', 'continue', 'reboot', 'key')) for c in dev.calls)
        )

    def test_each_protocol_check_fails_with_a_clear_detail(self):
        cases = [
            (dict(hello=V1_HELLO.replace('proto=%s' % P.ids.VERSION, 'proto=2.0')), 'hello', 'this pass speaks 1.x'),
            (dict(faults={'hello-text'}), 'hello', 'differs'),
            (dict(faults={'unsupported'}), 'unsupported', 'unknown type 0x5f: ok, expected unsupported'),
            (dict(faults={'refused'}), 'refused', 'state, written 0; expected refused'),
            (dict(faults={'leak'}), 'refused', 'exists after the refused write'),
            (dict(faults={'bad-request'}), 'bad-request', 'one byte short: refused, expected bad_request'),
            (dict(faults={'link'}), 'continue-link', 'ok, expected link; the menu may have closed'),
            (dict(faults={'gone'}), 'continue-link', 'diag.status: no reply'),
            (dict(faults={'short-read'}), 'read-speed', 'file.read 1024 B at 4096: ok, 100 B (expected 1024)'),
            (dict(faults={'stage'}), 'stage-speed', 'stage.data 1024 B at 1024: ok, received 2049'),
            (dict(faults={'end'}), 'refused', 'stage.end: ok, expected state (staging discarded)'),
        ]
        for kwargs, step, detail in cases:
            with self.subTest(step=step, detail=detail):
                dev = FakeDevice(**dict(dict(hello=V1_HELLO), **kwargs))
                self.assertEqual(self.run_pass(dev=dev), dp.EXIT_FAIL)
                rows = {r['step']: r for r in self.protocol_rows()}
                self.assertEqual(rows[step]['result'], 'FAIL')
                self.assertIn(detail, rows[step]['detail'])
                for later in PROTOCOL_STEPS[PROTOCOL_STEPS.index(step) + 1 :]:
                    self.assertEqual(rows[later]['detail'], 'after FAIL of protocol/%s' % step)
                self.assertEqual(self.rows()[('boot', 'dry-run')]['result'], 'SKIP')
                self.assertEqual(self.rows()[('end', 'keepalive')]['result'], 'PASS')
                if 'end' not in dev.faults:
                    self.assertIsNone(dev.staging)  # discarded even after the FAIL

    def run_install(self, image, hello, version='0.1.0'):
        with patch.object(dp.host, 'load_build', return_value=(b'image', ['b', 'LDR ok'])):
            return self.run_pass(
                ['--install', str(self.build(image, version=version)), '--stages', 'protocol'],
                dev=FakeDevice(hello=hello),
            )

    def test_image_version_and_bitmap_after_install(self):
        self.assertEqual(self.run_install(IMAGE, V1_HELLO), dp.EXIT_PASS, self.out)
        build = self.rows()[('protocol', 'build')]
        self.assertEqual(build['result'], 'PASS')
        self.assertIn(
            'image %s, version 0.1.0 (product) is the installed build; cmds %s'
            % (IMAGE, V1_HELLO.split('cmds=')[1][:32]),
            build['detail'],
        )
        order = [(r['stage'], r['step']) for r in self.summary['steps']]
        self.assertLess(order.index(('install', 'install-restart')), order.index(('protocol', 'hello')))
        for image, version, hello, step, detail in (
            (
                '0badc0de',
                '0.1.0',
                V1_HELLO,
                'build',
                'image=%s, expected 0badc0de: the installed build is not the one running' % IMAGE,
            ),
            (IMAGE, '0.2.0', V1_HELLO, 'build', 'version=0.1.0, expected 0.2.0'),
            (IMAGE, '0.1.0', V1_HELLO.replace(IMAGE, 'unknown'), 'build', 'image=unknown, expected %s' % IMAGE),
            (
                IMAGE,
                '0.1.0',
                P.hello_text('0.1.0', IMAGE, 'product', set()),
                'build',
                'cmds %032x, expected %032x'
                % (P.answered(set()), P.answered(dp.xyf_build.usb_switches('xyf-product'))),
            ),
            (IMAGE, '0.1.0', PRE_V1_HELLO, 'hello', 'installed build %s speaks v1, but the HELLO is pre-v1' % IMAGE),
        ):
            with self.subTest(image=image, step=step, detail=detail):
                self.assertEqual(self.run_install(image, hello, version), dp.EXIT_FAIL)
                row = self.rows()[('protocol', step)]
                self.assertEqual(row['result'], 'FAIL')
                self.assertIn(detail, row['detail'])

    def test_big_replies_only_on_request(self):
        dev = FakeDevice(hello=V1_HELLO)
        dev.files[dp.PASS_FILE] = dp.pass_file('earlier')
        self.assertEqual(self.run_pass(['--stages', 'protocol', '--big-replies'], dev=dev), dp.EXIT_PASS, self.out)
        rows = {r['step']: r for r in self.protocol_rows()}
        self.assertIn('whole-file reads of 2500 B (reply > read=): 3 ok', rows['read-speed']['detail'])
        self.assertIn('4096 B chunks: 64 KiB', rows['stage-speed']['detail'])
        self.assertEqual(set(self.summary['protocol']['stage_kib_s']), {'1024', '4096'})
        self.assertEqual(self.summary['protocol']['whole_read']['bytes'], 2500)
        self.assertFalse(dev.completed)
        self.assertIsNone(dev.staging)
        # A 4 KiB frame without a reply (Test 034) is a FAIL; the staging is still discarded.
        dev = FakeDevice(hello=V1_HELLO, faults={'timeout-4k'})
        self.assertEqual(self.run_pass(['--stages', 'protocol', '--big-replies'], dev=dev), dp.EXIT_FAIL)
        self.assertIn('stage.data: no reply', self.rows()[('protocol', 'stage-speed')]['detail'])
        self.assertIsNone(dev.staging)
        self.assertIn('CAN HANG THE DEVICE', self.help_text())

    def help_text(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
            dp.parse_args(['--help'])
        return ' '.join(out.getvalue().split())

    def test_wire_refuses_anything_that_could_change_the_device(self):
        sent = []

        class Link:
            def call(self, kind, payload, timeout):
                sent.append(kind)
                return 0, struct.pack('<I', 2) if kind == P.PUT_DATA else b''

            raw = call

        wire = dp.Wire(Link(), [])
        u32 = lambda v: struct.pack('<I', v)
        for kind, payload, raw in (
            (P.INSTALL, bytes(32), False),
            (P.UNINSTALL, bytes(32), False),
            (P.SET_DEFAULT, b'opxy-121\0', False),
            (P.REBOOT, u32(1), False),
            (P.KEY, u32(51) + u32(1), False),
            (0x33, u32(1), True),
            (0x60, b'HANG', True),
            (P.MTP_TEST, u32(0), False),
            (P.FILE_WRITE, BASE.encode() + b'x\0' + bytes(32), False),
            (P.CONTINUE, b'', False),
            (P.CONTINUE, u32(0), False),
            (P.CONTINUE, u32(1), False),
            (P.PUT_END, b'', False),
            (P.PUT_DATA, u32(0) + b'x', False),
            (P.HELLO, b'', True),
            (0x5F, b'', False),
            (0x80, b'', True),
        ):
            with self.subTest(kind=kind, payload=payload), self.assertRaises(ValueError):
                wire.call(kind, payload, raw=raw)
        self.assertEqual(sent, [])
        wire.call(P.PUT_BEGIN, P.pack(dp.BEGIN_REQ, dict(size=3, crc32=0, sha256=bytes(32))))
        wire.call(P.PUT_DATA, u32(0) + b'ab')
        with self.assertRaises(ValueError):  # would complete the staging
            wire.call(P.PUT_DATA, u32(2) + b'c')
        wire.call(P.PUT_END)
        with self.assertRaises(ValueError):  # one discard per staging
            wire.call(P.PUT_END)
        wire.call(0x5F, b'', raw=True)
        self.assertEqual(sent, [P.PUT_BEGIN, P.PUT_DATA, P.PUT_END, 0x5F])

    def test_host_link_keeps_xyf_hosts_cmds_check(self):
        class Answer2:
            """Answers every frame with status 2 (unsupported)."""

            def __init__(self):
                self.parser, self.out = P.Parser(), b''

            def write(self, data):
                for kind, seq, _ in self.parser.feed(data):
                    self.out += P.encode(kind | P.REPLY, seq, struct.pack('<I', 2))

            def read(self, timeout):
                data, self.out = self.out, b''
                return data

            def close(self):
                pass

        dev = dp.host.Device(Answer2(), on_log=None)
        dev.hello_info = P.Hello(V1_HELLO)
        link = dp.HostLink(dev)
        with self.assertRaises(dp.host.Unsupported):
            link.call(0x5F, b'', 1.0)
        self.assertEqual(link.raw(0x5F, b'', 1.0), (2, b''))
        self.assertIs(dev.hello_info.text, V1_HELLO)  # restored after the raw frame
        with self.assertRaises(dp.host.Unsupported):
            link.call(0x5F, b'', 1.0)


class SilentExitTest(unittest.TestCase):
    """#50: a run never stops without saying why."""

    def test_only_keepalive_loops_are_stopped(self):
        with tempfile.TemporaryDirectory() as tmp:
            for args in (
                ['/bin/sh', '/repo/scripts/keepalive.sh', tmp],
                ['sh', 'scripts/keepalive.sh', tmp],
                ['bash', '-c', 'scripts/keepalive.sh', tmp, '>/dev/null'],
            ):
                self.assertEqual(dp.keepalive_dir(args), tmp, args)
            for args in (
                ['grep', '-v', '-e', 'keepalive.sh', '-e', tmp],  # a filter on the script's output
                ['/bin/zsh', '-c', 'pgrep', '-fl', 'keepalive.sh', '&&', 'python3'],  # the shell that ran it
                ['sh', 'scripts/keepalive.sh', tmp + '/missing'],
                ['sh', 'scripts/keepalive.sh'],
                [],
            ):
                self.assertIsNone(dp.keepalive_dir(args), args)

    def test_an_error_after_the_folder_is_written_there(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(dp, 'EVIDENCE', [Path(tmp)]),
            patch.object(dp.signal, 'signal'),
        ):

            def broken():
                raise BrokenPipeError('the output pipe closed')

            with self.assertRaises(BrokenPipeError):
                dp.run_main(broken)
            self.assertIn('BrokenPipeError: the output pipe closed', (Path(tmp) / 'error.txt').read_text())
            (Path(tmp) / 'error.txt').unlink()
            self.assertEqual(dp.run_main(lambda: dp.EXIT_NEED), dp.EXIT_NEED)
            self.assertFalse((Path(tmp) / 'error.txt').exists())


if __name__ == '__main__':
    unittest.main()
