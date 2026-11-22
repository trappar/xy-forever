"""B's actual .tfw source owner and audited dispatcher with a synthetic signed package."""

import hashlib
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_tfw import container
from test_tfw_os import PUBLIC_KEY, build_zip, signed, standard

from xy_forever import hostbuild, sim

ROOT = Path(__file__).resolve().parents[1]


class TfwImportSourceTests(unittest.TestCase):
    def test_binds_and_releases_authenticated_source(self):
        with tempfile.TemporaryDirectory(prefix='xyf-tfw-source-') as temporary:
            directory = Path(temporary)
            bridge = directory / 'bridge'
            target = ('tfw_import_source', 'file_engine', 'boot_services')
            native = ('tfw_check', 'tfw', 'tfw_signature', 'p224', 'aes', 'hash')
            dispatcher = directory / 'tfw_device_check.cpp'
            dispatcher.write_text(sim.rewrite(dispatcher.name, (ROOT / 'target' / dispatcher.name).read_text()))
            build = hostbuild.run(
                [
                    '/usr/bin/clang++',
                    '-std=c++11',
                    '-O2',
                    '-Wall',
                    '-Wextra',
                    '-Werror',
                    '-Wno-unknown-attributes',
                    '-D__bfin__=1',
                    '-DXYF_TFW_CHECK=1',
                    '-I',
                    str(ROOT / 'target'),
                    str(ROOT / 'tests/tfw_import_source_bridge.cpp'),
                    str(dispatcher),
                    *[str(ROOT / 'target' / (name + '.cpp')) for name in target],
                    *[str(ROOT / 'native' / (name + '.cpp')) for name in native],
                    '-o',
                    str(bridge),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(build.returncode, 0, build.stderr)
            archive, _, _ = build_zip(standard())
            package = signed(container(archive))
            inputs = [directory / name for name in ('synthetic.tfw', 'clear.zip', 'public-key', 'ciphertext-hash')]
            for path, content in zip(inputs, (package, archive, PUBLIC_KEY, hashlib.sha256(package[0x380:]).digest())):
                path.write_bytes(content)
            result = subprocess.run([str(bridge), *map(str, inputs)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
