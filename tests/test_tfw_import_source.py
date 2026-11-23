"""B's actual .tfw source owner and audited dispatcher with a synthetic package."""

import subprocess
import tempfile
import unittest
from pathlib import Path

import xyf_hosttest
from test_tfw import container
from test_tfw_os import build_zip, signed, standard

ROOT = Path(__file__).resolve().parents[1]


class TfwImportSourceTests(unittest.TestCase):
    def test_binds_and_releases_the_source(self):
        with tempfile.TemporaryDirectory(prefix='xyf-tfw-source-') as temporary:
            directory = Path(temporary)
            bridge = xyf_hosttest.program('tfw_import_source')  # with the simulator's rewrite of the dispatcher
            archive, _, _ = build_zip(standard())
            package = signed(container(archive))
            inputs = [directory / name for name in ('synthetic.tfw', 'clear.zip')]
            for path, content in zip(inputs, (package, archive)):
                path.write_bytes(content)
            result = subprocess.run([str(bridge), *map(str, inputs)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
