"""`xyf`, the developer package's command (xy_forever/xyf.py, #284): the protocol check at connect, the mod a kit
gives, `xyf dev`'s refusals before any device work and its hand-over to moddev (whose device flow
test_sim_moddev.py proves in the simulator), `--watch` on new and rebuilt kits, and `xyf logs` copying only what is
new. No device and no Node; one test runs `xyf logs` against the simulator (skipped without the private stock
images)."""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from xy_forever import device_client, moddev, sim, xyf
from xy_forever import usb_proto as P

UNAVAILABLE = sim.available() or (None if sim.STOCK_OS.is_file() else 'private stock 1.2.1 os.ldr absent')

HELLO = 'XYF1;service=usb;role=b;proto=%s;version=0.1.0;image=00000000;mode=product'


def dev_args(**kw):
    values = dict(only=None, sdk=None, base=None, unverified=False, wait=1.0, verbose=False, watch=False)
    values.update(kw)
    return SimpleNamespace(**values)


def write_kit(folder, machines, extensions=(), done=True):
    folder.mkdir(parents=True, exist_ok=True)
    kit = dict(
        machines=[dict(id=i, origin=o) for i, o in machines], extensions=[dict(id=i, origin=o) for i, o in extensions]
    )
    (folder / 'kit.json').write_text(json.dumps(kit))
    if done:
        (folder / 'opxy-builder.html').write_text('<html>')
    return folder


class ProtocolTest(unittest.TestCase):
    def test_the_range_and_the_lines(self):
        for proto in ('1.30', '1.31', '1.99'):
            self.assertIsNone(xyf.protocol_problem(HELLO % proto))
        older = xyf.protocol_problem(HELLO % '1.21')
        self.assertEqual(
            older,
            'the OP-XY speaks XY Forever protocol 1.21; xyf needs 1.30 or a later 1.x. Update XY Forever at '
            'https://xy-forever.trappar.com (Manage → Device → Update)',
        )
        self.assertIn('protocol 1; xyf needs 1.30', xyf.protocol_problem('XYF1;service=usb;role=b;proto=1'))  # pre-v1
        newer = xyf.protocol_problem(HELLO % '2.0')
        self.assertIn('protocol 2.0; xyf needs 1.30 or a later 1.x. Update xyf: pip install -U xy-forever', newer)

    def test_a_mismatch_closes_the_port(self):
        port = mock.Mock()
        dev = SimpleNamespace(t=port)
        with mock.patch('xy_forever.device_client.connect', return_value=(dev, 'sim', HELLO % '1.29')):
            with self.assertRaisesRegex(xyf.Incompatible, 'protocol 1.29'):
                xyf.checked_connect(1.0)
        port.close.assert_called_once()


class DevTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='xyf-test-'))
        self.addCleanup(shutil.rmtree, self.tmp)
        self.sdk = self.tmp / 'open-xy'
        (self.sdk / 'builder').mkdir(parents=True)
        (self.sdk / 'builder/core.mjs').write_text('')
        self.lines = []
        which = mock.patch('xy_forever.xyf.shutil.which', return_value='/usr/bin/node')
        which.start()
        self.addCleanup(which.stop)

    def dev(self, kit, splitter=None, **kw):
        args = dev_args(sdk=self.sdk, **kw)
        return xyf.dev_kit(args, kit, self.lines.append, splitter or (lambda k, out, sdk: Path(out) / 'gen'))

    def test_the_mod_a_kit_gives(self):
        self.assertEqual(xyf.choose(dict(machines=[dict(id='mine', origin='personal')])), 'mine')
        kit = dict(machines=[dict(id='fm2op', origin='official'), dict(id='mine', origin='personal')])
        self.assertEqual(xyf.choose(kit), 'mine')
        kit['extensions'] = [dict(id='cpu', origin='official')]
        self.assertEqual(xyf.choose(kit, 'cpu'), 'ext-cpu')
        self.assertEqual(xyf.choose(kit, 'fm2op'), 'fm2op')
        with self.assertRaisesRegex(
            xyf.Refused, r"no machine or extension 'x' in the kit \(it holds fm2op, mine, cpu\)"
        ):
            xyf.choose(kit, 'x')
        kit['machines'].append(dict(id='also', origin='personal'))
        with self.assertRaisesRegex(
            xyf.Refused, r'the kit holds 2 mods \(mine, also\): name the one to try with --only'
        ):
            xyf.choose(kit)

    def test_refusals_before_anything_runs(self):
        kit = write_kit(self.tmp / 'kit', [('mine', 'personal')])
        self.assertEqual(self.dev(self.tmp), 2)
        self.assertEqual(xyf.dev_kit(dev_args(sdk=self.tmp), kit, self.lines.append), 2)
        with mock.patch('xy_forever.xyf.shutil.which', return_value=None):
            self.assertEqual(self.dev(kit), 2)
        self.assertEqual(
            self.lines,
            [
                'xyf dev: refused: %s holds no kit.json: give the folder `sdk.py kit --personal --out FOLDER` wrote'
                % self.tmp,
                'xyf dev: refused: no Open-XY checkout at %s (builder/core.mjs): run xyf from your Open-XY checkout '
                'or give --sdk DIR' % self.tmp,
                'xyf dev: refused: ' + xyf.NODE,
            ],
        )

    def test_a_split_that_fails(self):
        from xy_forever import modkit

        def broken(kit, out, sdk):
            raise modkit.KitError('core.mjs driver failed: boom')

        self.assertEqual(self.dev(write_kit(self.tmp / 'kit', [('mine', 'personal')]), broken), 1)
        self.assertEqual(self.lines, ['xyf dev: failed: the kit could not be split: core.mjs driver failed: boom'])

    def test_moddev_runs_the_split_mod(self):
        seen = []

        def command(run, connect, out, name):
            seen.append((run.package_dir.name, run.package_dir.parent.name, run.base, run.unverified, name))
            connect(1.0)

        kit = write_kit(self.tmp / 'kit', [('mine', 'personal')])
        with (
            mock.patch.object(moddev, 'command', command),
            mock.patch('xy_forever.device_client.connect', return_value=(mock.Mock(), 'sim', HELLO % '1.21')),
        ):
            self.assertEqual(self.dev(kit, base='opxy-121', unverified=True), 2)
        self.assertEqual(seen, [('mine', 'gen', 'opxy-121', True, 'xyf dev')])
        self.assertEqual(len(self.lines), 1)
        self.assertTrue(self.lines[0].startswith('xyf dev: the OP-XY speaks XY Forever protocol 1.21'), self.lines)


class WatchTest(unittest.TestCase):
    def test_new_and_rebuilt_kits_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = write_kit(root / 'mine-001', [('mine', 'personal')])
            ran, lines = [], []
            steps = [
                lambda: write_kit(root / 'mine-002', [('mine', 'personal')], done=False),  # not finished yet
                lambda: (root / 'mine-002' / 'opxy-builder.html').write_text('<html>'),
                lambda: None,  # nothing new: nothing runs
                lambda: os.utime(old / 'opxy-builder.html', ns=(1 << 62, 1 << 62)),  # rebuilt in place
            ]

            def sleep(seconds):
                self.assertEqual(seconds, xyf.POLL_S)
                if not steps:
                    raise KeyboardInterrupt
                steps.pop(0)()

            def run(args, kit, out):
                ran.append(kit.name)
                return 0

            code = xyf.watch(dev_args(folder=root, watch=True), lines.append, sleep, run)
        self.assertEqual(code, xyf.STOPPED)
        self.assertEqual(ran, ['mine-002', 'mine-001'])
        self.assertEqual(
            lines,
            [
                'xyf dev: watching %s for new kits (Ctrl-C stops)' % root,
                'kit %s' % (root / 'mine-002'),
                'kit %s' % old,
                'xyf dev: stopped',
            ],
        )

    def test_a_stop_inside_a_run_ends_the_watch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sleeps = [lambda: write_kit(root / 'k', [('mine', 'personal')])]
            code = xyf.watch(
                dev_args(folder=root),
                lambda line: None,
                lambda s: sleeps.pop(0)() if sleeps else self.fail('watched on after a stop'),
                lambda args, kit, out: xyf.STOPPED,
            )
        self.assertEqual(code, xyf.STOPPED)


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
