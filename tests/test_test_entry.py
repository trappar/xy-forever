"""scripts/test_entry.py: the entry it builds equals the device job's host twin (modpatch.entry_files, which
tests/test_mod_job.py holds to native/mod_job), and against the simulator: a half entry (os.ldr + info.txt of an
earlier run) completed, listed "content not installed", --boot (content switch, default, library.boot),
--cleanup refused while installed, then restored and removed. No device access."""
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
import zlib

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import xyf_host as host                              # noqa: E402
from xy_forever import modpatch as MP, package_records, sim, xyf_entry  # noqa: E402

spec = importlib.util.spec_from_file_location('test_entry', ROOT / 'scripts/test_entry.py')
te = importlib.util.module_from_spec(spec)
spec.loader.exec_module(te)
UNAVAILABLE = sim.available() or (None if sim.STOCK_OS.is_file() else 'private stock 1.2.1 os.ldr absent')
PROFILE, GENERATION = 'opxy-1.2.1', '0.0.6-370a949e96aa'
CONTENT = {'op1_factory.json': b'{"test": "catalog with fm2op"}\n', 'display/fm2op.svg': b'<svg/>\n',
           'presets/fm2op/default.json': b'{"p": 1}\n'}


def firmware_info(data, name, **more):
    return xyf_entry.manifest_text(dict(type='firmware', name=name, kind='open-xy', bytes=len(data),
                                        crc32='%08x' % (zlib.crc32(data) & 0xffffffff),
                                        sha256=hashlib.sha256(data).hexdigest(), **more)).encode()


class EntryFiles(unittest.TestCase):
    def test_equals_the_device_jobs_twin(self):
        image, needs = b'\x01ldr' * 1000, 'ab' * 32
        with tempfile.TemporaryDirectory() as tmp:
            gen = Path(tmp)
            (gen / 'base').mkdir()
            (gen / 'base/info.txt').write_text('type=mod-base\nprofile=%s\ngeneration=%s\nneeds_os=%s\n'
                                               % (PROFILE, GENERATION, needs))
            (gen / 'base/extensions.txt').write_text('')
            assembled = {'os.ldr': image, **{'content/' + p: b for p, b in CONTENT.items()}}
            ref = MP.entry_files(gen, assembled, ['fm2op'], name='Loader test')
        mine = te.entry_files(image, CONTENT, 'Loader test', needs, GENERATION, PROFILE, machines=1)
        self.assertEqual(mine, ref)
        self.assertEqual([l.split('  ')[1] for l in mine['files.sha256'].decode().splitlines()],
                         ['content/display/fm2op.svg', 'content/op1_factory.json', 'content/presets/fm2op/default.json',
                          'manifest.json', 'os.ldr'])

    def test_candidate_refuses_files_outside_loaded_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = Path(tmp)
            (c / 'content').mkdir()
            (c / 'content/op1_factory.json').write_text('{}')
            (c / 'os.ldr').write_bytes(b'x')
            (c / 'xy-forever/loaded/other').mkdir(parents=True)
            (c / 'xy-forever/loaded/other/a.oxl').write_bytes(b'a')
            with self.assertRaises(ValueError):
                te.candidate(c, 'loader-test')


@pytest.mark.sim
@unittest.skipIf(UNAVAILABLE, UNAVAILABLE)
class TestEntrySimulator(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='xyf-test-entry-')
        self.addCleanup(self.tmp.cleanup)
        work = Path(self.tmp.name)
        self.root = sim.seed(work / 'yaffs2')
        lib = self.root / 'xy-forever/firmware'
        stock = sim.STOCK_OS.read_bytes()
        catalog = (self.root / 'content/op1_factory.json').read_bytes()
        (lib / 'opxy-121-stock/info.txt').write_bytes(te.xyf_entry.manifest_text(dict(
            name='OP-XY 1.2.1', version='1.2.1', kind='official', bytes=len(stock),
            crc32='%08x' % (zlib.crc32(stock) & 0xffffffff))).encode())   # no sha256=, as on the device
        (lib / 'opxy-121-stock/content.sha256').write_text('%s  op1_factory.json\n' % hashlib.sha256(catalog).hexdigest())
        (lib / 'mods-a').mkdir()
        (lib / 'mods-a/os.ldr').write_bytes(stock)
        (lib / 'mods-a/info.txt').write_bytes(firmware_info(stock, 'Mods: 7', needs_os=hashlib.sha256(stock).hexdigest(),
                                                            generation=GENERATION, profile=PROFILE))
        (lib / 'loader-test').mkdir()                                          # the earlier failed run's half entry
        (lib / 'loader-test/os.ldr').write_bytes(stock)
        (lib / 'loader-test/info.txt').write_bytes(firmware_info(stock, 'Mods test: image', version='image'))
        self.cand = work / 'candidate'
        for path, data in CONTENT.items():
            (self.cand / 'content' / path).parent.mkdir(parents=True, exist_ok=True)
            (self.cand / 'content' / path).write_bytes(data)
        (self.cand / 'os.ldr').write_bytes(stock)
        (self.cand / 'xy-forever/loaded/loader-test').mkdir(parents=True)
        (self.cand / 'xy-forever/loaded/loader-test/sift.oxl').write_bytes(b'oxl' * 100)
        self.sim = sim.Simulator(self.root, work / 'bootpart.bin', build_dir=sim.build(), log=work / 'log.txt').start()
        self.addCleanup(self.sim.stop)

    def run_script(self, *argv):
        out = io.StringIO()
        folder = Path(tempfile.mkdtemp(dir=self.tmp.name))
        try:
            with contextlib.redirect_stdout(out):
                code = te.main(['--sim', self.sim.address, '--candidate', str(self.cand)]
                               + list(argv), sleep=lambda s: self.sim.advance(int(s * 1000)), folder=folder)
        finally:
            host.SIM = None
        return code, out.getvalue()

    def wait_listening(self):
        deadline = time.monotonic() + 60.0
        while time.monotonic() < deadline:
            if self.sim.status()['listening']:
                return
            time.sleep(0.01)
        self.fail('the menu did not come back: %s' % self.sim.status())

    def test_ask_before_usb_control_stops_it_first(self):
        """#65: with Ask before USB control on (HELLO confirm=1), every mode stops before it sends anything else."""
        (self.root / 'xy-forever/settings.txt').write_bytes(b'format=1\nconfirm=1\n')
        self.assertTrue(self.sim.control('set ask 1').get('ok'))             # the setting as the file says
        self.sim.control('power play')                                        # read again at the next start
        self.wait_listening()
        before = MP.tree(self.root / 'xy-forever/firmware/loader-test')
        for mode in ((), ('--boot',), ('--cleanup',)):
            code, out = self.run_script(*mode)
            self.assertEqual(code, 1, out)
            self.assertIn('FAIL: Switch off Settings > Ask before USB control on the device first', out)
        self.assertEqual(MP.tree(self.root / 'xy-forever/firmware/loader-test'), before)

    def test_upload_boot_restore_cleanup(self):
        entry = self.root / 'xy-forever/firmware/loader-test'
        code, out = self.run_script()
        self.assertEqual(code, 0, out)
        self.assertIn('os.ldr kept', out)
        self.assertIn('needs_os taken from firmware/mods-a', out)
        stock = sim.STOCK_OS.read_bytes()
        want = te.entry_files(stock, CONTENT, 'Loader test', hashlib.sha256(stock).hexdigest(),
                              GENERATION, PROFILE)
        self.assertEqual(MP.tree(entry), want)
        self.assertTrue((self.root / 'xy-forever/loaded/loader-test/sift.oxl').is_file())
        self.assertEqual((self.root / 'content/op1_factory.json').read_bytes(), b'{"xyf_sim": "stand-in op1_factory.json"}\n')

        code, out = self.run_script('--boot')
        self.assertEqual(code, 0, out)
        self.assertEqual(MP.tree(self.root / 'content'), CONTENT)
        self.assertIn('entry=loader-test', (self.root / 'xy-forever/content-state.txt').read_text())
        self.sim.control('power play')                                        # a power cycle holding PLAY
        self.wait_listening()

        code, out = self.run_script('--cleanup')
        self.assertEqual(code, 1, out)
        self.assertTrue(entry.is_dir())
        host.SIM = self.sim.address                                         # the restore the orchestrator runs
        try:
            code, out, err = te.dp.HostDevice().command(['set-default', 'opxy-121', '--yes-write'], 60.0)
        finally:
            host.SIM = None
        self.assertEqual(code, 0, out + err)
        self.assertEqual(MP.tree(self.root / 'content'), {'op1_factory.json': b'{"xyf_sim": "stand-in op1_factory.json"}\n'})
        code, out = self.run_script('--cleanup')
        self.assertEqual(code, 0, out)
        self.assertFalse(entry.exists())
        self.assertFalse((self.root / 'xy-forever/loaded/loader-test').exists())

    def test_content_state_larger_than_one_read(self):
        # content-state.txt lists every content file (a device's is ~8 KB): read whole, not in one range.
        lines = ['state=installed', 'entry=loader-test'] + ['file=presets/m%03d/default.json\t%s' % (i, '0' * 64)
                                                            for i in range(120)]
        record = ''.join(l + '\n' for l in lines).encode()
        self.assertGreater(len(record), 4 * host.READ_RANGE)
        (self.root / 'xy-forever/content-state.txt').write_bytes(record)
        code, out = self.run_script('--cleanup')
        self.assertEqual(code, 1, out)
        self.assertIn("'entry': 'loader-test'", out)                        # refused for what the record says
        self.assertIn('set another default first', out)
        self.assertTrue((self.root / 'xy-forever/firmware/loader-test').is_dir())

    def test_needs_os_from_the_stock_image_when_no_info_has_it(self):
        info = self.root / 'xy-forever/firmware/mods-a/info.txt'
        info.write_bytes(b''.join(l + b'\n' for l in info.read_bytes().splitlines() if not l.startswith(b'needs_os=')))
        code, out = self.run_script()
        self.assertEqual(code, 0, out)
        self.assertIn('SHA-256 of firmware/opxy-121-stock/os.ldr read back', out)
        needs = hashlib.sha256(sim.STOCK_OS.read_bytes()).hexdigest()
        self.assertEqual(package_records.read_values(self.root / 'xy-forever/firmware/loader-test')['needs_os'], needs)


if __name__ == '__main__':
    unittest.main()
