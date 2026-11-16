"""scripts/mod_boot.py against the device simulator (plan 5.4e0, 2.4d): upload of an image into the
scratch entry (read back, catalog, default unchanged), the content delta (catalog saved first, put back
byte-identical by --cleanup), the boot once through library.boot and the cleanup. Synthetic image;
skipped without the simulator. The real kit's assembly runs when its packages are present."""
import contextlib
import importlib.util
import io
from pathlib import Path
import shutil
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
import xyf_host as host                              # noqa: E402
from xy_forever import paths, sim                    # noqa: E402
from test_ldr import image                           # noqa: E402

spec = importlib.util.spec_from_file_location('mod_boot', ROOT / 'scripts/mod_boot.py')
mb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mb)
UNAVAILABLE = sim.available() or (None if sim.STOCK_OS.is_file() else 'private stock 1.2.1 os.ldr absent')
ENTRY = '/yaffs2/xy-forever/firmware/xyf-mods-test'


@unittest.skipIf(UNAVAILABLE, UNAVAILABLE)
class ModBootSimulator(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='xyf-mod-boot-')
        self.addCleanup(self.tmp.cleanup)
        work = Path(self.tmp.name)
        self.root = sim.seed(work / 'yaffs2')
        self.sim = sim.Simulator(self.root, work / 'bootpart.bin', build_dir=sim.build(), log=work / 'log.txt').start()
        self.addCleanup(self.sim.stop)
        self.image = work / 'os.ldr'
        self.image.write_bytes(image([(4, 0x11a00000, 4096, 0, bytes(range(256)) * 16)], entry=0x11a00000))
        paths.SESSIONS.mkdir(parents=True, exist_ok=True)
        self.sessions = tempfile.TemporaryDirectory(prefix='mod-boot-test-', dir=paths.SESSIONS)
        self.addCleanup(self.sessions.cleanup)

    def run_script(self, *argv, device=None):
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                code = mb.main(['--sim', self.sim.address] + list(argv), device=device, sessions=self.sessions.name,
                               sleep=lambda s: self.sim.advance(int(s * 1000)))     # the device clock
        finally:
            host.SIM = None
        rows = {l.split()[0]: l.split()[1] for l in out.getvalue().splitlines()[1:] if '/' in l.split()[0]}
        return code, rows, out.getvalue()

    def test_upload_boot_once_slots_and_cleanup(self):
        code, rows, out = self.run_script('--image', str(self.image), '--keep-evidence')
        self.assertEqual(code, 0, out)
        self.assertEqual({k for k, v in rows.items() if v == 'PASS'},
                         {'check/check', 'assemble/assemble', 'upload/entry-free', 'upload/put-os', 'upload/put-info',
                          'upload/ls-entry', 'upload/read-back', 'upload/library'}, out)
        self.assertEqual(rows['accept/dry-run-named'], 'SKIP')
        folder = self.root / ENTRY[len('/yaffs2/'):]
        self.assertEqual((folder / 'os.ldr').read_bytes(), self.image.read_bytes())
        info = (folder / 'info.txt').read_text()
        self.assertIn('type=firmware\nname=Mods test: image\n', info)
        self.assertIn('kind=open-xy\nbytes=%d\n' % self.image.stat().st_size, info)
        self.assertEqual(mb.recorded_default(self.sessions.name, True), 'opxy-121')

        code, rows, out = self.run_script('--image', str(self.image))              # exists: refused
        self.assertEqual((code, rows['upload/entry-free']), (1, 'FAIL'), out)

        code, rows, out = self.run_script('--image', str(self.image), '--replace', '--boot-once')
        self.assertEqual(code, 0, out)
        self.assertEqual([rows[s] for s in ('boot-once/boot-prepare', 'boot-once/boot')], ['PASS'] * 2, out)
        self.assertRegex((Path(self.tmp.name) / 'log.txt').read_text(), r'library\.boot: catalog entry \d once')
        self.assertIn('RECOVERY: power off, wait 5 s', out)
        self.assertIn('default (opxy-121) is unchanged', out)
        state = sorted(p for p in (self.root / 'xy-forever').glob('state-*.txt'))
        self.assertEqual([host.parse_slot(p.read_bytes())[1].get('once') for p in state], [None] * len(state))
        self.sim.control('power play')                                        # a power cycle holding PLAY
        self.wait_listening()

        code, rows, out = self.run_script('--cleanup')
        self.assertEqual(code, 0, out)
        self.assertEqual((rows['cleanup/cleanup-state'], rows['cleanup/cleanup-remove']), ('PASS', 'PASS'), out)
        self.assertFalse(folder.exists())

    def wait_listening(self):
        """Until the menu's USB listens again (host time bounds a hang only: B comes up in ~20 ms)."""
        deadline = time.monotonic() + 60.0
        while time.monotonic() < deadline:
            if self.sim.status()['listening']:
                return
            time.sleep(0.01)
        self.fail('the menu did not come back: %s' % self.sim.status())

    def test_content_delta_saved_written_and_restored(self):
        """--content: the device catalog is saved (SHA-256) before anything is written; the delta goes
        to content/ (folders made, an existing svg saved and replaced); --cleanup puts the catalog back
        byte-identical, restores the replaced svg and removes what it added. Without a catalog on the
        device, nothing is written."""
        content = self.root / 'content'
        official = (content / 'op1_factory.json').read_bytes()
        (content / 'display').mkdir()
        (content / 'display' / 'other.svg').write_bytes(b'<svg>other</svg>')
        (content / 'display' / 'xyf-old.svg').write_bytes(b'<svg>old</svg>')     # replaced, then put back
        delta = Path(self.tmp.name) / 'delta'
        (delta / 'display').mkdir(parents=True)
        (delta / 'presets' / 'xyf-test').mkdir(parents=True)
        (delta / 'display' / 'xyf-test.svg').write_bytes(b'<svg>new</svg>')
        (delta / 'display' / 'xyf-old.svg').write_bytes(b'<svg>new old</svg>')
        (delta / 'presets' / 'xyf-test' / 'default.json').write_bytes(b'{"preset": 1}\n')
        (delta / 'op1_factory.json').write_bytes(b'{"assembled": true}\n')
        code, rows, out = self.run_script('--image', str(self.image), '--content', str(delta))
        self.assertEqual(code, 0, out)
        self.assertEqual((rows['content/content-save'], rows['content/content-put']), ('PASS', 'PASS'), out)
        self.assertEqual((content / 'op1_factory.json').read_bytes(), b'{"assembled": true}\n')
        self.assertEqual((content / 'display' / 'xyf-old.svg').read_bytes(), b'<svg>new old</svg>')
        self.assertEqual((content / 'presets' / 'xyf-test' / 'default.json').read_bytes(), b'{"preset": 1}\n')
        record = mb.recorded_content(self.sessions.name, True)
        self.assertEqual(record['catalog']['sha256'], mb.hashlib.sha256(official).hexdigest())
        self.assertEqual((Path(record['folder']) / record['catalog']['saved']).read_bytes(), official)
        self.assertEqual(sorted(record['added_dirs']), ['/yaffs2/content/presets', '/yaffs2/content/presets/xyf-test'])

        code, rows, out = self.run_script('--cleanup')
        self.assertEqual(code, 0, out)
        self.assertEqual(rows['cleanup/cleanup-content'], 'PASS', out)
        self.assertEqual((content / 'op1_factory.json').read_bytes(), official)            # byte-identical
        self.assertEqual(sorted(p.name for p in content.iterdir()), ['display', 'op1_factory.json'])
        self.assertEqual(sorted(p.name for p in (content / 'display').iterdir()), ['other.svg', 'xyf-old.svg'])
        self.assertEqual((content / 'display' / 'xyf-old.svg').read_bytes(), b'<svg>old</svg>')
        self.assertFalse((self.root / ENTRY[len('/yaffs2/'):]).exists())

        (content / 'op1_factory.json').unlink()                                # nothing to save: nothing written
        code, rows, out = self.run_script('--image', str(self.image), '--content', str(delta))
        self.assertEqual((code, rows['content/content-save'], rows['content/content-put']), (1, 'FAIL', 'SKIP'), out)
        self.assertIn('could not be saved first', out)
        self.assertEqual(sorted(p.name for p in content.iterdir()), ['display'])
        self.assertFalse((content / 'display' / 'xyf-test.svg').exists())


@unittest.skipUnless((mb.PACKAGES / 'base/info.txt').is_file() and mb.CATALOG.is_file() and sim.STOCK_OS.is_file() and
                     mb.modpatch.default_official(mb.PACKAGES).is_file(),
                     'published-kit packages, stock os.ldr, op1_factory.json or the official content.sha256 absent')
class ModBootAssemble(unittest.TestCase):
    def test_fm2op_assembles_and_passes_the_checks(self):
        paths.SESSIONS.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='mod-boot-test-', dir=paths.SESSIONS) as sessions:
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                code = mb.main(['--stages', 'assemble'], sessions=sessions)
            self.assertEqual(code, 0, out.getvalue())
            folder = next(Path(sessions).glob('*-mod-boot-*'))
            data = (folder / 'assembled/os.ldr').read_bytes()
            self.assertEqual(mb.image_problems(data), [])
            self.assertTrue((folder / 'assembled/content/op1_factory.json').is_file())
            shutil.rmtree(folder)


if __name__ == '__main__':
    unittest.main()
