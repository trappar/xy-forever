"""Cancellation through the signed TFW reader, ZIP members and OS writer."""
from pathlib import Path
import random
import subprocess
import tempfile
import unittest

from test_ldr import image
from test_tfw_os import OS, VERSION, GOOD_VERSION, PUBLIC_KEY, member, package, signed

from xy_forever import hostbuild

ROOT = Path(__file__).resolve().parents[1]


class TfwWorkTests(unittest.TestCase):
    def test_cancellation_discards_results_and_stops_io(self):
        with tempfile.TemporaryDirectory(prefix='xyf-tfw-work-') as temporary:
            directory = Path(temporary)
            bridge = directory / 'bridge'
            modules = ('tfw_os', 'tfw', 'tfw_signature', 'p224', 'aes', 'hash', 'inflate', 'ldr', 'zip', 'package_policy')
            hostbuild.run(['/usr/bin/clang++', '-std=c++11', '-O2', '-Wall', '-Wextra', '-Werror',
                           str(ROOT / 'tests/tfw_work_bridge.cpp'),
                           *[str(ROOT / 'native' / (name + '.cpp')) for name in modules],
                           '-o', str(bridge)], check=True, capture_output=True)
            code = b'\x01\x02'
            payload = bytes(range(256)) * 400
            loader = image([(4, 0x11a00000, len(code), 0, code), (4, 0x80001000, len(payload), 0, payload)],
                           entry=0x11a00000)
            content = random.Random(4301).randbytes(7000)
            rows = [member(VERSION, GOOD_VERSION), member(OS, loader),
                    member('firmware/blackfin/content/example', content)]
            path, public_key = directory / 'synthetic.tfw', directory / 'public-key'
            path.write_bytes(signed(package(rows)))
            public_key.write_bytes(PUBLIC_KEY)
            result = subprocess.run([str(bridge), str(path), str(public_key)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
