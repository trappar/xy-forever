"""#135 in the simulator: a library with no base entry, `xyf_host.py library-add ID OS_LDR ... --content DIR` adds the
stock image with its content set (content.sha256 + firmware.version, hashed here from the content folder), and a mod
build on it then starts and finishes with no manual step. Without --content the add says what is missing and the build
refusal names the fix. Synthetic official content (test_modpatch.OFFICIAL: each file's bytes are its path) in the
official package layout. Needs clang++ and the private stock 1.2.1 images (skipped without them). No device access."""
import contextlib
import io
from pathlib import Path
import shutil
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
import xyf_host as host                                                   # noqa: E402
from xy_forever import modsend as MS, sim                                  # noqa: E402
import test_sim_mod_job as J                                               # noqa: E402
from test_modpatch import OFFICIAL, official_text                         # noqa: E402
from test_sim_mods_page import synthetic_setup                            # noqa: E402


@unittest.skipIf(J.UNAVAILABLE, J.UNAVAILABLE)
class FreshLibrary(J.Base, unittest.TestCase):
    base = 'modbase'
    unverified = True

    @classmethod
    def setUpClass(cls):
        synthetic_setup(cls)
        entry = cls.seeded / 'xy-forever/firmware' / cls.base
        cls.image = (entry / 'os.ldr').read_bytes()
        shutil.rmtree(entry)                                                # no base entry: library-add makes it
        (cls.seeded / 'xy-forever/state-a.txt').write_bytes(sim.slot_text('opxy-121', 2))
        package = cls.work / 'official'                                     # firmware/{firmware.version, blackfin/content/}
        cls.content = package / 'firmware/blackfin/content'
        for rel in OFFICIAL:
            (cls.content / rel).parent.mkdir(parents=True, exist_ok=True)
            (cls.content / rel).write_bytes(rel.encode())
        (package / 'firmware/firmware.version').write_text('1.2.1+0\n')

    def host(self, *args):
        self.dev.t.close()                                                  # the tool opens its own session
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = host.run(['--sim', self.sim.address, '--timeout', '10'] + list(args))
        self.dev = host.Device(host.TcpPort(self.sim.address), on_log=None)
        return code, out.getvalue()

    def add(self, *extra):
        ldr = self.dir / 'os.ldr'
        ldr.write_bytes(self.image)
        return self.host('library-add', self.base, str(ldr), '--name', 'OP-XY 1.2.1', '--version', '1.2.1', *extra,
                         '--yes-write')

    def test_add_with_content_then_a_build_runs(self):
        code, text = self.add('--content', str(self.content))
        self.assertEqual(code, 0, text)
        self.assertIn('library-add %s: ok\n' % self.base, text)
        self.assertNotIn('note:', text)
        entry = self.entry(self.base)
        self.assertEqual(sorted(p.name for p in entry.iterdir()),
                         ['content.sha256', 'files.sha256', 'firmware.version', 'info.txt', 'os.ldr'])
        self.assertEqual((entry / 'content.sha256').read_text(), official_text(sorted(OFFICIAL)))
        self.assertEqual((entry / 'firmware.version').read_text(), '1.2.1+0\n')
        self.assemble_and_compare(['alpha'])                                # started and done, no manual step

    def test_add_without_content_says_what_is_missing(self):
        code, text = self.add()
        self.assertEqual(code, 0, text)
        self.assertIn('library-add %s: ok (no content list)' % self.base, text)
        self.assertIn('note: firmware/%s gets no content.sha256 + firmware.version' % self.base, text)
        self.assertIn('Give --content DIR', text)
        self.assertEqual(sorted(p.name for p in self.entry(self.base).iterdir()), ['files.sha256', 'info.txt', 'os.ldr'])
        code, text = self.host('mods', 'apply', '--base', self.base, '--generation',
                               '%s/%s' % (self.profile, self.generation), 'alpha', '--unverified')
        self.assertEqual(code, MS.NOT_FOUND, text)
        self.assertIn("missing the base's content.sha256; library-add --content adds it", text)


if __name__ == '__main__':
    unittest.main()
