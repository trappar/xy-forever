"""scripts/test_entry.py: the entry it builds equals the device job's host twin (modpatch.entry_files, which
tests/test_mod_job.py holds to native/mod_job), and against the simulator: a half entry (os.ldr + info.txt of an
earlier run) completed, listed "content not installed", --boot (content switch, default, library.boot),
--cleanup refused while installed, then restored and removed. No device access."""

import importlib.util
import tempfile
import unittest
from pathlib import Path

from xy_forever import modpatch as MP
from xy_forever import sim

ROOT = Path(__file__).resolve().parents[1]

spec = importlib.util.spec_from_file_location('test_entry', ROOT / 'scripts/test_entry.py')
te = importlib.util.module_from_spec(spec)
spec.loader.exec_module(te)
UNAVAILABLE = sim.available() or (None if sim.STOCK_OS.is_file() else 'private stock 1.2.1 os.ldr absent')
PROFILE, GENERATION = 'opxy-1.2.1', '0.0.6-370a949e96aa'
CONTENT = {
    'op1_factory.json': b'{"test": "catalog with fm2op"}\n',
    'display/fm2op.svg': b'<svg/>\n',
    'presets/fm2op/default.json': b'{"p": 1}\n',
}


class EntryFiles(unittest.TestCase):
    def test_equals_the_device_jobs_twin(self):
        image, needs = b'\x01ldr' * 1000, 'ab' * 32
        with tempfile.TemporaryDirectory() as tmp:
            gen = Path(tmp)
            (gen / 'base').mkdir()
            (gen / 'base/info.txt').write_text(
                'type=mod-base\nprofile=%s\ngeneration=%s\nneeds_os=%s\n' % (PROFILE, GENERATION, needs)
            )
            (gen / 'base/extensions.txt').write_text('')
            assembled = {'os.ldr': image, **{'content/' + p: b for p, b in CONTENT.items()}}
            ref = MP.entry_files(gen, assembled, ['fm2op'], name='Loader test')
        mine = te.entry_files(image, CONTENT, 'Loader test', needs, GENERATION, PROFILE, machines=1)
        self.assertEqual(mine, ref)
        self.assertEqual(
            [l.split('  ')[1] for l in mine['files.sha256'].decode().splitlines()],
            [
                'content/display/fm2op.svg',
                'content/op1_factory.json',
                'content/presets/fm2op/default.json',
                'manifest.json',
                'os.ldr',
            ],
        )

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


if __name__ == '__main__':
    unittest.main()
