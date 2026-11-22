"""5.6 in the simulator: `xyf_host.py mods dev`, the mod developers' loop (push what changed by SHA-256, build on
the device, make it the default with its content, --restore). Synthetic packages (test_sim_mod_job's kit),
nothing on the device's mods/ at first. Needs clang++ and the private stock 1.2.1 images (skipped without
them). No device access."""
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

from xy_forever import moddev

PRESET = 'content/presets/alpha/default.json'


class Watch(unittest.TestCase):
    """--watch: a pass, then another after a file changes (device calls replaced)."""

    def test_reruns_on_change(self):
        work = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, work, True)
        (work / 'base').mkdir()
        (work / 'alpha').mkdir()
        (work / 'alpha' / 'x').write_bytes(b'1')
        runs = []

        def once(*a, **k):
            runs.append(1)
            return 0, {}

        def sleep(_):
            if len(runs) == 1:
                (work / 'alpha' / 'x').write_bytes(b'22')
        with mock.patch.object(moddev, 'dev_once', once):
            code = moddev.watch(None, 'p', None, work, ['alpha'], (), None, out=lambda line: None, sleep=sleep,
                                rounds=2)
        self.assertEqual((code, len(runs)), (0, 2))


if __name__ == '__main__':
    unittest.main()
