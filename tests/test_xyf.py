"""`xyf`, the developer package's command (xy_forever/xyf.py, dev_entry.py; #284): the protocol check at connect;
`xyf dev`'s check that a build is Open-XY's assembly of stock OS 1.2.1, the library entry it makes, its refusals
before any device work and its hand-over to the connection; `--watch` on new and rebuilt builds; `xyf stock-os`
copying the checked stock image; `xyf logs` copying only what is new; the Windows serial port. No device; one test
runs `xyf logs` against the simulator (skipped without the private stock images); the push and the boot once in the
simulator: test_sim_xyf_dev.py."""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest
from test_ldr import header, image
from test_modpatch import profile_for, stock

from xy_forever import dev_entry, device_client, moddev, package_records, sim, transport, xyf
from xy_forever import usb_proto as P
from xy_forever.ldr import parse_ldr

UNAVAILABLE = sim.available() or (None if sim.STOCK_OS.is_file() else 'private stock 1.2.1 os.ldr absent')
HELLO = 'XYF1;service=usb;role=b;proto=%s;version=0.1.0;image=00000000;mode=product'


def assemble(base, profile, module=b'\x11' * 64):
    """Open-XY's assembly (tooling/firmware.py assemble): base with the profile's edits, the module record before
    FINAL, FIRST's length fixed."""
    patched = bytearray(base)
    for patch in profile['patches']:
        offset, length = int(patch['file_offset'], 16), patch['length']
        after = bytes.fromhex(patch['after']) if len(patch.get('after', '')) == 2 * length else b'\xee' * length
        patched[offset : offset + length] = after
    blocks = parse_ldr(base)
    at = blocks[-1].offset
    body = bytes(patched[16:at]) + header(4, profile['module_base'], len(module)) + module + bytes(patched[at:])
    return header(0x5004, blocks[0].target, 0, len(body)) + body


class Synthetic:
    """A synthetic stock image and its profile (test_modpatch's), standing in for OS 1.2.1 (the pin patched)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='xyf-test-'))
        self.addCleanup(shutil.rmtree, self.tmp)
        self.stock = stock()
        self.profile = profile_for(self.stock)
        self.image = assemble(self.stock, self.profile)
        for name, value in (('STOCK_SHA256', self.profile['stock_sha256']), ('STOCK_BYTES', len(self.stock))):
            patcher = mock.patch.object(dev_entry, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.sdk = self.tmp / 'open-xy'
        (self.sdk / 'profiles').mkdir(parents=True)
        (self.sdk / 'profiles' / 'opxy-1.2.1.json').write_text(json.dumps(self.profile))
        self.stock_path = self.tmp / 'stock-1.2.1' / 'os.ldr'
        self.stock_path.parent.mkdir()
        self.stock_path.write_bytes(self.stock)

    def build(self, name='mine-001', image=None, content=None, done=True):
        """An `sdk.py build --out` folder."""
        folder = self.tmp / 'builds' / name
        (folder / 'candidate').mkdir(parents=True, exist_ok=True)
        (folder / 'candidate' / 'os.ldr').write_bytes(self.image if image is None else image)
        for rel, data in (content or {'op1_factory.json': b'{"catalog": 1}', 'display/mine.svg': b'<svg/>'}).items():
            path = folder / 'candidate' / 'content' / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        if done:
            (folder / 'verification.json').write_text('{}')
        return folder


def dev_args(**kw):
    values = dict(sdk=None, stock_os=None, wait=1.0, verbose=False, watch=False)
    values.update(kw)
    return SimpleNamespace(**values)


class ProtocolTest(unittest.TestCase):
    def test_the_range_and_the_lines(self):
        for proto in ('1.31', '1.32', '1.99'):  # xyf dev: 1.31's boot once makes the base's content set first
            self.assertIsNone(xyf.protocol_problem(HELLO % proto))
        older = xyf.protocol_problem(HELLO % '1.30')
        self.assertEqual(
            older,
            'the OP-XY speaks XY Forever protocol 1.30; xyf needs 1.31 or a later 1.x. Update XY Forever at '
            'https://xy-forever.trappar.com (Manage > Device > Update)',
        )
        self.assertIn('protocol 1; xyf needs 1.31', xyf.protocol_problem('XYF1;service=usb;role=b;proto=1'))  # pre-v1
        newer = xyf.protocol_problem(HELLO % '2.0')
        self.assertIn('protocol 2.0; xyf needs 1.31 or a later 1.x. Update xyf: pip install -U xy-forever', newer)
        logs = xyf.NEEDS['logs']  # logs and stock-os: file.list (1.5) and file.read
        self.assertIsNone(xyf.protocol_problem(HELLO % '1.5', logs))
        self.assertIn('protocol 1.4; xyf needs 1.5 or a later 1.x', xyf.protocol_problem(HELLO % '1.4', logs))

    def test_each_command_asks_for_its_own_range(self):
        with mock.patch('xy_forever.device_client.connect', return_value=(mock.Mock(), 'sim', HELLO % '1.30')):
            self.assertIsNotNone(xyf.connector('logs')(1.0))
            with self.assertRaisesRegex(xyf.Incompatible, 'needs 1.31'):
                xyf.connector('dev')(1.0)

    def test_a_mismatch_closes_the_port(self):
        port = mock.Mock()
        dev = SimpleNamespace(t=port)
        with mock.patch('xy_forever.device_client.connect', return_value=(dev, 'sim', HELLO % '1.29')):
            with self.assertRaisesRegex(xyf.Incompatible, 'protocol 1.29'):
                xyf.checked_connect(1.0)
        port.close.assert_called_once()


class CheckTest(Synthetic, unittest.TestCase):
    def test_what_the_device_admits_passes(self):
        for data in (self.image, self.stock):  # Open-XY's build, and any other image the device boots
            dev_entry.check_image(data)

    def test_what_the_device_refuses_is_refused_first(self):
        entry = parse_ldr(self.stock)[-1].target
        cases = [
            (b'not an image', 'is 12 bytes: XY Forever boots 32 to'),
            (b'\0' * 64, 'is not an OS image'),
            (image([(4, 0x8A000000, 4, 0, b'\0\0\0\0'), (4, entry, 4, 0, b'\0' * 4)], entry), 'never loads an image'),
            (image([(4, 0x70000000, 4, 0, b'\0\0\0\0'), (4, entry, 4, 0, b'\0' * 4)], entry), 'never loads an image'),
            (image([(4, 0x11A10000, 4, 0, b'\0' * 4)], 0x11A10000), 'starts at 0x11a10000'),
        ]
        for data, pattern in cases:
            with self.subTest(pattern=pattern), self.assertRaisesRegex(dev_entry.Refused, pattern):
                dev_entry.check_image(data)

    def test_which_it_is(self):
        self.assertTrue(dev_entry.open_xy_build(self.image, self.stock, self.profile))
        changed = bytearray(self.image)
        changed[112 + 0x90] ^= 1  # a byte of the stock text record outside every edit (test_modpatch's TEXT_AT)
        for other in (bytes(changed), self.stock, assemble(self.stock, dict(self.profile, module_base=0x8D000000))):
            self.assertFalse(dev_entry.open_xy_build(other, self.stock, self.profile))
        self.assertEqual(dev_entry.which(self.image, self.stock_path, self.sdk), dev_entry.OPEN_XY)
        self.assertEqual(dev_entry.which(bytes(changed), self.stock_path, self.sdk), dev_entry.CUSTOM)
        self.assertEqual(dev_entry.which(self.image, self.tmp / 'none', self.sdk), dev_entry.CUSTOM)  # no stock here
        self.assertEqual(dev_entry.which(self.image, self.stock_path, self.tmp), dev_entry.CUSTOM)  # no profile

    def test_the_build_and_the_entry(self):
        image, content = dev_entry.read_build(self.build())
        self.assertEqual((image, sorted(content)), (self.image, ['display/mine.svg', 'op1_factory.json']))
        self.assertEqual(dev_entry.read_build(self.build() / 'candidate')[0], self.image)  # the candidate folder too
        self.assertEqual(dev_entry.read_build(self.build() / 'candidate' / 'os.ldr'), (self.image, {}))  # a file
        files = dev_entry.entry_files(image, content, dev_entry.OPEN_XY)
        self.assertEqual(
            sorted(files),
            ['content/display/mine.svg', 'content/op1_factory.json', 'files.sha256', 'manifest.json', 'os.ldr'],
        )
        fields = package_records.parse(files['manifest.json'], 'manifest.json')
        digest = dev_entry.sha256(self.image)
        self.assertEqual(
            {k: fields[k] for k in ('type', 'kind', 'needs_os', 'generation', 'profile', 'sha256')},
            dict(
                type='firmware',
                kind='open-xy',
                needs_os=dev_entry.STOCK_SHA256,
                generation='dev-' + digest[:12],
                profile='opxy-1.2.1',
                sha256=digest,
            ),
        )
        plain = dev_entry.entry_files(self.stock, {})  # a custom image without content: a plain firmware entry
        self.assertEqual(sorted(plain), ['files.sha256', 'manifest.json', 'os.ldr'])
        fields = package_records.parse(plain['manifest.json'], 'manifest.json')
        self.assertEqual((fields['kind'], 'generation' in fields, 'needs_os' in fields), ('custom', False, False))
        with self.assertRaisesRegex(dev_entry.Refused, 'cannot be a library entry'):
            dev_entry.entry_files(image, {'bootloader.dev': b'x'})
        (self.build() / 'candidate' / 'notes.txt').write_text('?')
        with self.assertRaisesRegex(dev_entry.Refused, 'files xyf dev does not know: notes.txt'):
            dev_entry.read_build(self.build())
        with self.assertRaisesRegex(dev_entry.Refused, 'holds no candidate/os.ldr or os.ldr'):
            dev_entry.read_build(self.tmp)


class DevTest(Synthetic, unittest.TestCase):
    def dev(self, folder, connect=None, **kw):
        lines = []
        args = dev_args(**{'sdk': self.sdk, 'stock_os': self.stock_path, **kw})
        code = xyf.dev_build(args, folder, lines.append, connect or (lambda *a, **k: self.fail('connected')))
        return code, lines

    def test_a_refusal_before_the_device(self):
        self.assertEqual(
            self.dev(self.build('broken', image=b'\0' * 64)),
            (2, ['xyf dev: refused: the os.ldr is not an OS image (Invalid header/checksum at 0x0)']),
        )

    def test_any_image_reaches_the_connection(self):
        def connect(timeout, log, waiting):
            waiting()
            raise xyf.Incompatible('the OP-XY speaks XY Forever protocol 1.21; ...')

        for build in (self.build(), self.build('custom', image=self.stock, content={})):
            with self.subTest(build=build.name):
                code, lines = self.dev(build, connect, stock_os=self.tmp / 'none')  # no stock: still pushed
                self.assertEqual(
                    (code, lines), (2, [moddev.POWER, 'xyf dev: the OP-XY speaks XY Forever protocol 1.21; ...'])
                )


class WatchTest(Synthetic, unittest.TestCase):
    def test_new_and_rebuilt_builds_only(self):
        old = self.build('mine-001')
        root, ran, lines = old.parent, [], []
        steps = [
            lambda: self.build('mine-002', done=False),  # not finished yet
            lambda: (root / 'mine-002' / 'verification.json').write_text('{}'),
            lambda: None,  # nothing new: nothing runs
            lambda: os.utime(old / 'verification.json', ns=(1 << 62, 1 << 62)),  # rebuilt in place
        ]

        def sleep(seconds):
            self.assertEqual(seconds, xyf.POLL_S)
            if not steps:
                raise KeyboardInterrupt
            steps.pop(0)()

        def run(args, build, out):
            ran.append(build.name)
            return 0

        self.assertEqual(xyf.watch(dev_args(folder=root, watch=True), lines.append, sleep, run), xyf.STOPPED)
        self.assertEqual(ran, ['mine-002', 'mine-001'])
        self.assertEqual(
            lines,
            [
                'xyf dev: watching %s for new builds (Ctrl-C stops)' % root,
                'build %s' % (root / 'mine-002'),
                'build %s' % old,
                'xyf dev: stopped',
            ],
        )

    def test_a_stop_inside_a_run_ends_the_watch(self):
        root = self.tmp / 'builds'
        root.mkdir()
        sleeps = [lambda: self.build('k')]
        code = xyf.watch(
            dev_args(folder=root),
            lambda line: None,
            lambda s: sleeps.pop(0)() if sleeps else self.fail('watched on after a stop'),
            lambda args, build, out: xyf.STOPPED,
        )
        self.assertEqual(code, xyf.STOPPED)


class StockOsTest(Synthetic, unittest.TestCase):
    def test_the_checked_copy(self):
        manifest = package_records.document(
            dict(
                type='firmware',
                name='OP-XY 1.2.1',
                kind='official',
                bytes=len(self.stock),
                crc32='00000000',
                sha256=dev_entry.STOCK_SHA256,
            )
        )
        lib = '/yaffs2/xy-forever/firmware/'
        dev = FakeDevice(
            {
                lib + 'opxy-121/manifest.json': manifest,
                lib + 'opxy-121/os.ldr': self.stock,
                lib + 'mods-a/manifest.json': b'{}',
                lib + 'mods-a/os.ldr': b'other',
            }
        )
        target, lines = self.tmp / 'out' / 'os.ldr', []
        args = SimpleNamespace(out=target, wait=1.0, verbose=False)
        with mock.patch.object(moddev, 'ready'):
            self.assertEqual(xyf.stock_os(args, lines.append, lambda *a, **k: (dev, 'sim', HELLO % '1.30')), 0)
        self.assertEqual(target.read_bytes(), self.stock)
        self.assertEqual(len(lines), 1)
        self.assertRegex(
            lines[0],
            r'^xyf stock-os: %s \(%d bytes, SHA-256 checked, from library entry opxy-121\) in '
            % (target, len(self.stock)),
        )
        lines.clear()
        self.assertEqual(xyf.stock_os(args, lines.append, lambda *a, **k: self.fail('connected')), 0)
        self.assertEqual(lines, ['xyf stock-os: %s is stock OS 1.2.1 already' % target])
        dev.files[lib + 'opxy-121/os.ldr'] = self.stock[:200] + b'?' + self.stock[201:]  # read back wrong
        lines.clear()
        args.out = self.tmp / 'again.ldr'
        with mock.patch.object(moddev, 'ready'):
            self.assertEqual(xyf.stock_os(args, lines.append, lambda *a, **k: (dev, 'sim', HELLO % '1.30')), 1)
        self.assertFalse(args.out.exists())

    def test_where_open_xy_keeps_it(self):
        with mock.patch.object(xyf.sys, 'platform', 'darwin'):
            self.assertEqual(
                xyf.default_stock(), Path.home() / 'Library/Application Support/OPXY-SDK/private/stock-1.2.1/os.ldr'
            )
        with mock.patch.object(xyf.sys, 'platform', 'win32'), mock.patch.dict(os.environ, LOCALAPPDATA='C:/L'):
            self.assertEqual(xyf.default_stock(), Path('C:/L/OPXY-SDK/private/stock-1.2.1/os.ldr'))


class WindowsPortTest(unittest.TestCase):
    def test_reads_and_writes_through_pyserial(self):
        import serial

        class Port:
            def __init__(self):
                self.timeout, self.waits, self.data, self.sent = None, [], b'XF', []

            def read(self, n):
                self.waits.append(self.timeout)
                out, self.data = self.data[:n], self.data[n:]
                return out

            def write(self, data):
                if data == b'stall':
                    raise serial.SerialTimeoutException('stalled')
                self.sent.append(data)

            def close(self):
                self.sent.append('closed')

        with self.assertRaisesRegex(BaseException, 'tests may not open a real device: COM3'):
            transport.WindowsSerialPort('COM3', opener=lambda *a, **k: Port())
        port = object.__new__(transport.WindowsSerialPort)
        port.port = Port()
        self.assertEqual(port.read(0.2), b'XF')  # the first byte waited for, the rest taken at once
        self.assertEqual(port.port.waits, [0.2, 0])
        self.assertEqual(port.read(0.1), b'')
        port.write(b'hello')
        with self.assertRaises(TimeoutError):
            port.write(b'stall')
        inner = port.port
        port.close()
        port.close()
        self.assertEqual(inner.sent, [b'hello', 'closed'])


class FakeDevice:
    """file.list and ranged reads over {device path: bytes}; folders are the paths' prefixes."""

    def __init__(self, files):
        self.files = files
        self.t = mock.Mock()

    def list_dir(self, folder):
        entries = {}
        for path, data in self.files.items():
            if path.startswith(folder + '/'):
                name, _, rest = path[len(folder) + 1 :].partition('/')
                entries[name] = (1, 0, name.encode()) if rest else (0, len(data), name.encode())
        return (0, list(entries.values())) if entries else (P.ids.S_NOT_FOUND, [])

    def read_all(self, path, limit):
        if path not in self.files:
            return P.ids.S_NOT_FOUND, b''
        data = self.files[path]
        return (0, data) if len(data) <= limit else (P.STATUS_NAMES.index('too_large'), data[:limit])


class LogsTest(unittest.TestCase):
    def test_only_new_files_are_copied_and_text_is_shown(self):
        files = {
            '/yaffs2/logs/crash.txt': b'assert failed\nat engine.cpp:42\n',
            '/yaffs2/logs/old/trace.bin': b'\x00\x01\x02',
            '/yaffs2/xy-forever/log/prod-b.bin': b'\xff' * 10,
        }
        dev = FakeDevice(files)
        with tempfile.TemporaryDirectory() as tmp:
            out, lines = Path(tmp) / 'xyf-logs', []
            self.assertEqual(len(xyf.copy_new(dev, out, lines.append)), 3)
            self.assertEqual((out / 'logs/crash.txt').read_bytes(), files['/yaffs2/logs/crash.txt'])
            self.assertEqual((out / 'xy-forever/log/prod-b.bin').read_bytes(), b'\xff' * 10)
            self.assertIn('/yaffs2/logs/crash.txt (31 bytes): %s' % (out / 'logs/crash.txt'), lines)
            self.assertIn('  at engine.cpp:42', lines)
            self.assertEqual(lines[-1], 'xyf logs: 3 new of 3 on the OP-XY')
            lines.clear()
            files['/yaffs2/xy-forever/log/prod-b.bin'] = b'\xee' * (xyf.LOG_LIMIT + 5)  # the next boot's report
            self.assertEqual(xyf.copy_new(dev, out, lines.append), ['/yaffs2/xy-forever/log/prod-b.bin'])
            self.assertEqual(
                lines,
                [
                    '/yaffs2/xy-forever/log/prod-b.bin (%d bytes, the first %d): %s'
                    % (xyf.LOG_LIMIT + 5, xyf.LOG_LIMIT, out / 'xy-forever/log/prod-b.bin'),
                    'xyf logs: 1 new of 3 on the OP-XY',
                ],
            )

    def test_a_menu_that_never_answers(self):
        """The OP-XY left in its OS past --wait: the power line once, then what to do."""
        lines = []

        def connect(timeout, log, waiting):
            waiting()
            raise SystemExit('XY Forever USB service not found within 5 s (...)')

        args = SimpleNamespace(out=Path('unused'), wait=5.0, verbose=False)
        self.assertEqual(xyf.logs(args, lines.append, connect), 1)
        self.assertEqual(
            lines,
            [
                moddev.POWER,
                'xyf logs: the XY Forever menu did not answer within 5 s (--wait): power the OP-XY on holding PLAY, '
                'then run it again',
            ],
        )

    def test_the_power_line_and_a_mismatch(self):
        lines = []

        def connect(timeout, log, waiting):
            waiting()
            raise xyf.Incompatible('the OP-XY speaks XY Forever protocol 1.21; ...')

        args = SimpleNamespace(out=Path('unused'), wait=1.0, verbose=False)
        self.assertEqual(xyf.logs(args, lines.append, connect), 2)
        self.assertEqual(lines, [moddev.POWER, 'xyf logs: the OP-XY speaks XY Forever protocol 1.21; ...'])


@unittest.skipIf(UNAVAILABLE, UNAVAILABLE)
class LogsInTheSimulatorTest(unittest.TestCase):
    @pytest.mark.sim
    @pytest.mark.uses('sim/')
    def test_xyf_logs_on_the_current_device(self):
        """Kept: the one run of xyf's own connect against the device's code: today's HELLO passes the protocol check,
        and the logs are listed and read through file.list and ranged reads."""
        with tempfile.TemporaryDirectory(prefix='xyf-logs-') as tmp:
            root = sim.seed(Path(tmp) / 'yaffs2')
            (root / 'logs').mkdir()
            (root / 'logs' / 'crash.txt').write_text('exception at 0x8a800010\n')
            simulator = sim.Simulator(root, Path(tmp) / 'bootpart.bin', log=Path(tmp) / 'log.txt').start()
            self.addCleanup(simulator.stop)
            lines, args = [], SimpleNamespace(out=Path(tmp) / 'out', wait=5.0, verbose=False)
            with mock.patch.object(device_client, 'SIM', simulator.address):
                self.assertEqual(xyf.logs(args, lines.append), 0, lines)
            self.assertEqual(
                lines,
                [
                    '/yaffs2/logs/crash.txt (24 bytes): %s' % (Path(tmp) / 'out/logs/crash.txt'),
                    '  exception at 0x8a800010',
                    'xyf logs: 1 new of 1 on the OP-XY',
                ],
            )


if __name__ == '__main__':
    unittest.main()
