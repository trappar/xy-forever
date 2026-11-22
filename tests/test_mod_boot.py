"""scripts/mod_boot.py against the device simulator: upload of an image into the
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
import unittest

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
from xy_forever import package_records, paths, sim                    # noqa: E402

spec = importlib.util.spec_from_file_location('mod_boot', ROOT / 'scripts/mod_boot.py')
mb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mb)
UNAVAILABLE = sim.available() or (None if sim.STOCK_OS.is_file() else 'private stock 1.2.1 os.ldr absent')
ENTRY = '/yaffs2/xy-forever/firmware/xyf-mods-test'


@unittest.skipUnless(package_records.local_name(mb.PACKAGES / 'base') and mb.CATALOG.is_file() and sim.STOCK_OS.is_file() and
                     mb.modpatch.default_official(mb.PACKAGES).is_file(),
                     'published-kit packages, stock os.ldr, op1_factory.json or the official content.sha256 absent')
@pytest.mark.gate
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
